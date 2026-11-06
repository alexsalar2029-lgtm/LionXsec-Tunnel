# LIONXSEC

## Publisher Desktop

برنامهٔ فارسی آماده‌سازی محتوا و زمان‌بندی انتشار در تلگرام در پوشهٔ [`publisher`](publisher) قرار دارد.

- [راهنمای نصب و اجرای Windows Server 2016](publisher/WINDOWS-SERVER-2016_FA.md)
- [دریافت آخرین بسته با نصب خودکار WebView2](downloads/LIONXSEC-Publisher-Desktop-v1.2.2-Server2016-Fix2.zip)
- [راهنمای کامل برنامه](publisher/README_FA.md)
- [آزمون‌ها و محدودیت‌های بررسی‌شده](publisher/VALIDATION.md)

ZIP را دریافت و کامل استخراج کنید؛ سپس `START-LIONXSEC.cmd` را اجرا کنید. در ZIP کامل مخزن، برنامه داخل پوشهٔ `publisher` است. Python نسخهٔ ۶۴ بیتی 3.10 تا 3.13 و .NET Framework 4.6.2 یا جدیدتر لازم است. راه‌انداز در صورت نبودن WebView2، نصب‌کنندهٔ رسمی x64 را دانلود می‌کند، امضای مایکروسافت را بررسی و Runtime را نصب می‌کند. در سرور ۲۰۱۶ باید **Desktop Experience** و نشست دسکتاپ فعال داشته باشید.

این برنامه از WebView2 در ویندوز و Qt WebEngine در لینوکس استفاده می‌کند. آزمون‌های لینوکس انجام شده‌اند؛ اجرای واقعی روی Windows Server 2016 و ارتباط با حساب زندهٔ تلگرام نیاز به بررسی روی سیستم مقصد دارند.

## Tunnel

تنظیمات قبلی Xray برای GitHub Codespaces در [`.devcontainer`](.devcontainer) قرار دارند. این بخش مستقل از برنامهٔ Publisher است.
