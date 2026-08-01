"""
mikrotik_api.py
لایه ارتباط با RouterOS API (کتابخانه routeros_api / پکیج RouterOS-api)
تمام متدهای این کلاس سینکرونوس (blocking) هستند و باید همیشه از داخل
asyncio.to_thread(...) در بخش تلگرام‌بات فراخوانی شوند تا event loop قفل نشود.

نکته: اطلاعات اتصال (Host/User/Password/Port) هر بار مستقیم از Config خوانده
می‌شود (نه یک‌بار در سازنده)، چون ادمین می‌تواند این مقادیر را از داخل پنل
مدیریت ربات تغییر دهد و باید بدون نیاز به ری‌استارت ربات اعمال شود.

نکات مخصوص ساختار واقعی User Manager روی RouterOS 7.12.1 (تایید شده با CLI واقعی روتر):
- ساخت یوزر:   /user-manager/user/add          (فیلدها: name, password, shared-users)
- اختصاص پروفایل: /user-manager/user-profile/add  (فیلدها: user=<نام یوزر>, profile=<نام پروفایل>)
  یعنی برخلاف فرضیات اولیه، فیلد profile مستقیم روی خود یوزر نیست؛ یک جدول جداست
  که یوزر را (با نام، نه ID) به یک پروفایل از قبل ساخته‌شده وصل می‌کند.
- وضعیت هر اکانت از فیلد state در همان user-profile خوانده می‌شود
  (مثل running-active, used, expired) — فیلد disabled روی خود یوزر وجود ندارد.
- shared-users=1 روی خود یوزر  => هر اکانت فقط روی یک دستگاه همزمان قابل اتصال است.

⚠️ نام پروفایل‌ها (PROFILE_1_MONTH / PROFILE_2_MONTH در .env یا پنل تنظیمات) باید
دقیقاً با نام پروفایل‌های از قبل ساخته‌شده در User Manager > Profiles یکی باشد
(حساس به بزرگ/کوچک بودن حروف)، مثلاً '30Day' یا '60Day'.
"""

import logging
import routeros_api

from config import Config

logger = logging.getLogger(__name__)


class MikrotikError(Exception):
    """خطای عمومی مربوط به ارتباط یا عملیات میکروتیک"""
    pass


class DuplicateUserError(MikrotikError):
    """یوزر تکراری یا مشابه (بدون توجه به بزرگ/کوچک بودن حروف) در User Manager"""
    pass


class UserNotFoundError(MikrotikError):
    """یوزر مورد نظر پیدا نشد"""
    pass


class MikrotikManager:
    def _connect(self) -> routeros_api.RouterOsApiPool:
        """ساخت یک کانکشن جدید به روتر برای هر عملیات؛ همیشه با آخرین تنظیمات فعلی"""
        try:
            connection = routeros_api.RouterOsApiPool(
                Config.MIKROTIK_HOST,
                username=Config.MIKROTIK_USER,
                password=Config.MIKROTIK_PASSWORD,
                port=Config.MIKROTIK_PORT,
                use_ssl=Config.MIKROTIK_USE_SSL,
                ssl_verify=False,
                plaintext_login=True,
            )
            return connection
        except Exception as e:
            logger.error(f"Mikrotik connection failed: {e}")
            raise MikrotikError(f"اتصال به میکروتیک برقرار نشد (IP/پورت/یوزرنیم/پسورد را بررسی کنید): {e}")

    @staticmethod
    def _normalize(name: str) -> str:
        return (name or "").strip().lower()

    # ------------------------------------------------------------------
    # ساخت یوزر جدید (دو مرحله: ساخت یوزر + اختصاص پروفایل)
    # ------------------------------------------------------------------
    def add_vpn_user(self, username: str, password: str, profile: str, shared_users: int = 1) -> bool:
        """
        ساخت یوزر جدید در User Manager و اختصاص پروفایل (مدت اعتبار) به آن.
        profile باید دقیقاً با نام یکی از پروفایل‌های از قبل ساخته‌شده در
        User Manager > Profiles یکی باشد (مثلاً '30Day').
        shared_users تعداد دستگاه‌هایی است که این اکانت می‌تواند همزمان به آن
        وصل شود (پیش‌فرض ۱؛ با خرید کاربر اضافه بیشتر می‌شود).

        بررسی تکراری بودن یوزرنیم به‌صورت case-insensitive انجام می‌شود.
        اگر مرحله اختصاص پروفایل با خطا مواجه شود (مثلاً نام پروفایل اشتباه)،
        یوزر نیمه‌ساز حذف می‌شود تا اکانت بدون پروفایل باقی نماند.
        """
        connection = self._connect()
        try:
            api = connection.get_api()
            user_resource = api.get_resource('/user-manager/user')

            normalized_new = self._normalize(username)
            all_users = user_resource.get()
            for u in all_users:
                if self._normalize(u.get('name', '')) == normalized_new:
                    raise DuplicateUserError(
                        f"یوزر '{username}' با یوزر موجود '{u.get('name')}' مشابه است (تکراری)."
                    )

            # مرحله ۱: ساخت خود یوزر
            user_resource.add(
                name=username,
                password=password,
                **{'shared-users': str(max(1, shared_users))},
            )
            logger.info(f"VPN user '{username}' created (step 1/2).")

            # مرحله ۲: اختصاص پروفایل (مدت اعتبار) به یوزر
            try:
                profile_resource = api.get_resource('/user-manager/user-profile')
                profile_resource.add(user=username, profile=profile)
                logger.info(f"Profile '{profile}' assigned to '{username}' (step 2/2).")
            except Exception as profile_error:
                # اگر اختصاص پروفایل شکست خورد، یوزر نیمه‌ساز را پاک می‌کنیم تا
                # اکانت بدون اعتبار روی روتر باقی نماند
                logger.error(f"Failed to assign profile to '{username}', rolling back: {profile_error}")
                try:
                    leftover = user_resource.get(name=username)
                    if leftover:
                        user_resource.remove(id=leftover[0]['id'])
                except Exception:
                    logger.exception(f"Rollback also failed for user '{username}'")
                raise MikrotikError(
                    f"یوزر ساخته شد ولی اختصاص پروفایل '{profile}' با خطا مواجه شد "
                    f"(نام پروفایل را در تنظیمات بررسی کنید): {profile_error}"
                )

            return True

        except DuplicateUserError:
            raise
        except MikrotikError:
            raise
        except Exception as e:
            logger.error(f"Error adding vpn user '{username}': {e}")
            msg = str(e)
            if "already have" in msg.lower() or "duplicate" in msg.lower() or "already exists" in msg.lower():
                raise DuplicateUserError(f"یوزر '{username}' تکراری است.")
            raise MikrotikError(f"خطا در ساخت یوزر: {msg}")
        finally:
            connection.disconnect()

    # ------------------------------------------------------------------
    # حذف یوزر (به‌همراه رکوردهای پروفایل مرتبط)
    # ------------------------------------------------------------------
    def delete_vpn_user(self, username: str) -> bool:
        """پیدا کردن یوزر (case-insensitive) و حذف آن، به‌همراه هر رکورد user-profile مرتبط"""
        connection = self._connect()
        try:
            api = connection.get_api()
            user_resource = api.get_resource('/user-manager/user')

            normalized_target = self._normalize(username)
            all_users = user_resource.get()
            match = next((u for u in all_users if self._normalize(u.get('name', '')) == normalized_target), None)

            if not match:
                raise UserNotFoundError(f"یوزری با نام '{username}' پیدا نشد.")

            actual_name = match['name']

            # پاک‌سازی رکوردهای user-profile مرتبط (احتیاطی؛ اگر خطا داد نادیده گرفته می‌شود)
            try:
                profile_resource = api.get_resource('/user-manager/user-profile')
                related = profile_resource.get(user=actual_name)
                for entry in related:
                    profile_resource.remove(id=entry['id'])
            except Exception:
                logger.warning(f"Could not clean up user-profile entries for '{actual_name}' (non-fatal).")

            user_resource.remove(id=match['id'])
            logger.info(f"VPN user '{username}' removed.")
            return True

        except UserNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Error deleting vpn user '{username}': {e}")
            raise MikrotikError(f"خطا در حذف یوزر: {e}")
        finally:
            connection.disconnect()

    # ------------------------------------------------------------------
    # لیست یوزرها (با join به user-profile برای نمایش پروفایل/وضعیت واقعی)
    # ------------------------------------------------------------------
    def list_vpn_users(self) -> list:
        """دریافت لیست کامل یوزرهای User Manager، به‌همراه پروفایل و وضعیت فعلی هرکدام"""
        connection = self._connect()
        try:
            api = connection.get_api()
            user_resource = api.get_resource('/user-manager/user')
            profile_resource = api.get_resource('/user-manager/user-profile')

            users = user_resource.get()
            profile_links = profile_resource.get()

            # نگاشت نام یوزر -> آخرین رکورد پروفایلش (اگر چند رکورد تاریخی داشت، آخری را نگه می‌داریم)
            profile_by_user = {}
            for link in profile_links:
                profile_by_user[link.get('user', '')] = link

            result = []
            for u in users:
                name = u.get('name', '-')
                link = profile_by_user.get(name)
                result.append({
                    'name': name,
                    'profile': link.get('profile', 'بدون پروفایل') if link else 'بدون پروفایل',
                    'state': link.get('state', 'نامشخص') if link else 'بدون پروفایل',
                })
            return result

        except Exception as e:
            logger.error(f"Error listing vpn users: {e}")
            raise MikrotikError(f"خطا در دریافت لیست یوزرها: {e}")
        finally:
            connection.disconnect()
