# LIONXSEC

## Publisher Desktop

برنامهٔ فارسی آماده‌سازی محتوا و زمان‌بندی انتشار در تلگرام در پوشهٔ [`publisher`](publisher) قرار دارد.

- [راهنمای نصب و اجرای Windows Server 2016](publisher/WINDOWS-SERVER-2016_FA.md)
- [دریافت بستهٔ سورس و راه‌انداز مخصوص این اصلاح](downloads/LIONXSEC-Publisher-Desktop-v1.2.2-Server2016-Fix.zip)
- [راهنمای کامل برنامه](publisher/README_FA.md)
- [آزمون‌ها و محدودیت‌های بررسی‌شده](publisher/VALIDATION.md)

ZIP مخزن را از **Code → Download ZIP** دریافت و کامل استخراج کنید؛ سپس وارد پوشهٔ `publisher` شوید و `START-LIONXSEC.cmd` را اجرا کنید. Python نسخهٔ ۶۴ بیتی 3.10 تا 3.13 و Microsoft Edge WebView2 Evergreen Runtime نسخهٔ x64 لازم است. در سرور ۲۰۱۶ باید **Desktop Experience** و نشست دسکتاپ فعال داشته باشید.

این برنامه از WebView2 در ویندوز و Qt WebEngine در لینوکس استفاده می‌کند. آزمون‌های لینوکس انجام شده‌اند؛ اجرای واقعی روی Windows Server 2016 و ارتباط با حساب زندهٔ تلگرام نیاز به بررسی روی سیستم مقصد دارند.

## Tunnel

تنظیمات قبلی Xray برای GitHub Codespaces در [`.devcontainer`](.devcontainer) قرار دارند. این بخش مستقل از برنامهٔ Publisher است.
