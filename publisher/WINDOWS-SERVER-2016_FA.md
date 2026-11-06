# اجرای LIONXSEC Publisher روی Windows Server 2016

این نسخه مسیر اجرای ویندوز را برای استفاده از **Microsoft Edge WebView2** اصلاح می‌کند. نسخهٔ قبلی Qt 6.10 را اجباری می‌کرد؛ حداقل ویندوز اعلام‌شده برای Qt 6.10، ویندوز ۱۰ نسخهٔ 1809 است، در حالی که سرور ۲۰۱۶ بر پایهٔ نسخهٔ 1607 است. مایکروسافت سرور ۲۰۱۶ را در فهرست سیستم‌های پشتیبانی‌شدهٔ WebView2 آورده است.

این اصلاح در محیط لینوکس و با آزمون‌های انتخاب موتور ویندوز بررسی شده است. اجرای واقعی پنجره روی سرور ۲۰۱۶ در این محیط ممکن نیست؛ سازگاری نهایی باید روی سرور خودتان بررسی شود.

## نصب و اجرا

1. سرور باید **Windows Server 2016 با Desktop Experience** باشد. با Remote Desktop وارد دسکتاپ شوید. Server Core محیط مناسب این برنامهٔ گرافیکی نیست.
2. [Python برای ویندوز](https://www.python.org/downloads/windows/) نسخهٔ **3.10 تا 3.13، ۶۴ بیتی** را نصب کنید. گزینهٔ **Add Python to PATH** را فعال کنید. Python 3.12 نسخهٔ مورد استفاده در آزمون‌های این اصلاح بوده است.
3. از [صفحهٔ رسمی Microsoft WebView2](https://developer.microsoft.com/microsoft-edge/webview2/#download)، **Evergreen Standalone Installer → x64** را دریافت و نصب کنید. نصب مرورگر Edge به‌تنهایی جای Runtime را نمی‌گیرد. برای نصب خط فرمان، دستور رسمی `MicrosoftEdgeWebView2RuntimeInstallerX64.exe /silent /install` قابل استفاده است.
4. **.NET Framework 4.6.2 یا جدیدتر** باید فعال باشد؛ این نسخه معمولاً همراه سرور ۲۰۱۶ است. در صورت خطای .NET، نصب یا فعال‌سازی آن را بررسی کنید؛ .NET Framework 4.8 نیز مناسب است.
5. برنامهٔ قبلی را ببندید. ZIP جدید را در پوشه‌ای جدید و قابل نوشتن، مثل `C:\LIONXSEC`، کامل استخراج کنید. فایل را مستقیماً از داخل ZIP اجرا نکنید.
6. **START-LIONXSEC.cmd** را اجرا کنید و پنجرهٔ نصب را تا پایان دانلود وابستگی‌ها باز نگه دارید. نصب اول اینترنت می‌خواهد.
7. اگر در Remote Desktop پنجره خالی بود یا خطای گرافیکی دیدید، برنامه را ببندید و **START-LIONXSEC-SOFTWARE.cmd** را اجرا کنید.

لازم نیست فایل‌های برنامه را به‌صورت Administrator اجرا کنید؛ نصب سیستمی پیش‌نیازها ممکن است دسترسی مدیر بخواهد.

## اگر باز نشد

راه‌انداز جدید خروجی نصب، خطاهای import و کد خروج فرایند را در **startup-error.log** کنار خودش ثبت می‌کند. با خطا، پنجرهٔ فرمان باز می‌ماند. متن آخرین بخش این فایل برای تشخیص دقیق مشکل کافی است؛ مقادیر خصوصی احتمالی را پیش از اشتراک‌گذاری حذف کنید.

- **Python ... was not found:** Python ۶۴ بیتی را نصب کنید و پنجرهٔ فرمان تازه‌ای باز کنید.
- **Windows desktop initialization failed / desktop_renderer_unavailable:** WebView2 Runtime نسخهٔ x64 و .NET Framework را بررسی کنید. اگر Runtime را با حساب دیگری نصب کرده‌اید، آن را برای کاربر جاری یا همهٔ کاربران نصب کنید.
- **No matching distribution / خطای دانلود:** اتصال سرور به `pypi.org` و `files.pythonhosted.org` و نسخهٔ Python را بررسی کنید.
- **Cannot write startup-error.log:** بسته را به پوشه‌ای منتقل کنید که کاربر فعلی اجازهٔ نوشتن در آن دارد.
- **already running:** پنجرهٔ نسخهٔ قبلی را پیدا کنید و ببندید.

در این نسخه PySide6 روی ویندوز نصب یا بارگذاری نمی‌شود و موتور قدیمی Internet Explorer پذیرفته نمی‌شود. فایل SOFTWARE هر دو موتور WebView2 و Qt را با GPU غیرفعال اجرا می‌کند.

## اطلاعات و اجراهای واقعی

داده‌های قبلی در `%LOCALAPPDATA%\LIONXSEC\Publisher` باقی می‌مانند؛ این پوشه را برای رفع مشکل اجرا حذف نکنید. ابتدا باز شدن پنجره را بررسی کنید. ورود به تلگرام، انتخاب کانال و فعال‌کردن انتشار را بعداً طبق README_FA.md انجام دهید.

برنامه و نشست ویندوز باید باز بمانند؛ خروج از حساب ویندوز یا خاموش‌کردن سرور انتشار را متوقف می‌کند. این نسخه سرویس ویندوز نیست.

منابع: [Qt 6.10 supported platforms](https://doc.qt.io/qt-6.10/supported-platforms.html)، [WebView2 supported Windows versions](https://learn.microsoft.com/en-us/microsoft-edge/webview2/#supported-windows-versions).
