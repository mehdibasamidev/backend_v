# انتقال بک‌اند به VPS جدید (بکاپ و بازیابی PostgreSQL و MinIO)

این راهنما برای وقتی است که بک‌اند روی یک سرور کار می‌کند و می‌خواهید همان
داده‌ها را به سرور دیگری ببرید. دو اسکریپت کار اصلی را انجام می‌دهند:

- `scripts/backup.sh` روی سرور **قدیم** اجرا می‌شود و یک پوشهٔ
  `backups/<زمان>/` می‌سازد.
- `scripts/restore.sh` روی سرور **جدید** اجرا می‌شود و همان پوشه را داخل
  کانتینرهای سرور جدید می‌ریزد و شمارش رکوردها و فایل‌ها را با سرور قدیم
  مقایسه می‌کند.

نصب خود سرور جدید (کاربر deploy، Docker، ufw، clone، HAProxy) در
`README_FA.md` مرحله‌های ۱ تا ۴ آمده و اینجا تکرار نمی‌شود. توجه کنید که
نام سرویس‌ها در آن راهنما قدیمی است؛ نام‌های واقعی همان‌هایی هستند که در
`docker-compose.yml` می‌بینید (`spacedigital_vpn_db`، `spacedigital_vpn_minio`،
`spacedigital_vpn_django`، `spacedigital_vpn_telegram_bot`).

## چه چیزی منتقل می‌شود

| مورد | روش | توضیح |
|---|---|---|
| PostgreSQL | `pg_dump --format=custom` ← `db.dump` | همهٔ جدول‌ها: کاربران، اشتراک‌ها، فیش‌ها، تنظیمات ادمین، `django_migrations` |
| MinIO | آرشیو خام پوشهٔ `/data` ← `minio-data.tar.gz` | هر دو bucket (`media` و `private`) به‌همراه policy دانلود عمومی `media` که داخل `.minio.sys` است |
| `.env` | کپی ← `env.backup` | همهٔ رمزها و کلیدها؛ روی سرور جدید بازبینی می‌شود |
| `haproxy.cfg`، crontab | کپی ← `haproxy.cfg`، `crontab.txt` | فقط برای مرجع؛ خودتان نصبشان می‌کنید |
| `manifest.txt` | ساخته می‌شود | نسخه‌ها، digest imageها، تعداد رکورد هر جدول و تعداد فایل هر bucket |
| Redis | منتقل نمی‌شود | فقط channel layer چت است؛ دادهٔ ماندگار ندارد |
| static | منتقل نمی‌شود | موقع build داخل image ساخته می‌شود |

چیزهایی که خارج از این مخزن‌اند و اسکریپت‌ها به آن‌ها دست نمی‌زنند. قبل از
شروع تکلیف هرکدام را روشن کنید:

- **گواهی TLS**: `/etc/letsencrypt` و `/etc/haproxy/certs`. کپی‌کردنشان
  (پایین‌تر) بهتر از صدور دوباره است، چون تا وقتی DNS عوض نشده certbot روی
  سرور جدید کار نمی‌کند.
- **قوانین ufw** و فایروال provider (`sudo ufw status numbered` روی سرور قدیم).
- **پنل 3x-ui** (`XUI_PANEL_BASE_URL`): اگر روی همین سرور قدیم است، انتقال
  آن کار جداگانه‌ای است و این راهنما پوشش نمی‌دهد. اگر جای دیگری است، هیچ
  کاری لازم نیست؛ سرور جدید با همان آدرس و توکن به آن وصل می‌شود.
- **پراکسی خروجی تلگرام** (`TELEGRAM_PROXY_URL`): اگر به چیزی روی خود سرور
  قدیم اشاره می‌کند (`127.0.0.1` یا یک کانتینر)، باید روی سرور جدید هم باشد.
- **سایت‌های دیگر HAProxy**: `haproxy.conf` علاوه بر این API، دامنه‌های
  `spacedigital.top` (پورت ۸۰۸۱) و `bodyremix.ir` (پورت ۸۰۸۲) را هم مسیریابی
  می‌کند. اگر آن‌ها روی سرور جدید نیستند، backendشان down می‌ماند و ۵۰۳
  می‌گیرند؛ برای این API مشکلی نیست، فقط بدانید.

## نقشهٔ کار

مهاجرت را دو بار انجام دهید:

1. **تمرین** (بدون downtime): سرور قدیم کار می‌کند، یک بکاپ زنده می‌گیرید،
   روی سرور جدید بازیابی می‌کنید و بدون عوض‌کردن DNS تست می‌کنید. هر
   مشکلی (image، رمزها، policy مینیو، گواهی) اینجا معلوم می‌شود، نه وسط
   انتقال واقعی.
2. **انتقال نهایی** (چند دقیقه downtime): برنامه را روی سرور قدیم متوقف
   می‌کنید، بکاپ نهایی می‌گیرید، همان مراحل را تکرار می‌کنید و DNS را
   می‌چرخانید.

## قبل از شروع

1. سرور جدید را طبق `README_FA.md` مرحله‌های ۱ تا ۴ آماده کنید. هنوز
   `docker compose up` نزنید و برای گواهی هم certbot اجرا نکنید؛ گواهی‌ها
   را از سرور قدیم می‌آورید.

2. روی سرور جدید مخزن را clone کنید و **همان کدی را checkout کنید که سرور
   قدیم اجرا می‌کند**، به‌اضافهٔ این اسکریپت‌ها. `backup.sh` هش commit
   سرور قدیم را در `manifest.txt` می‌نویسد و `restore.sh` اگر commit سرور
   جدید فرق داشته باشد هشدار می‌دهد. هشدار وقتی مهم است که بین دو commit
   migration جدیدی باشد:

   ~~~bash
   git log --oneline <commit-قدیم>..HEAD -- '*/migrations/*'
   ~~~

   اگر خروجی خالی است، کد اپ عملاً یکی است و می‌توانید ادامه دهید. ارتقا
   به نسخهٔ جدیدتر را بعد از پایان انتقال انجام دهید، نه هم‌زمان.

3. اسکریپت‌ها باید روی سرور قدیم هم باشند. یا شاخه‌ای که آن‌ها را دارد
   pull کنید، یا فقط سه فایل را کپی کنید (به هیچ چیز دیگری وابسته نیستند):

   ~~~bash
   scp scripts/_lib.sh scripts/backup.sh scripts/restore.sh deploy@OLD_IP:/srv/vpn-backend/scripts/
   ~~~

4. SSH بین دو سرور را برقرار کنید (کلید کاربر deploy سرور قدیم را در
   `authorized_keys` سرور جدید بگذارید) تا `rsync` بدون رمز کار کند.

5. **یک روز قبل از انتقال نهایی** TTL رکوردهای A مربوط به
   `api.spacedigital.top`، `minio.spacedigital.top` و `api.bodyremix.ir` را
   به ۳۰۰ ثانیه یا کمتر کاهش دهید تا موقع جابه‌جایی، کلاینت‌ها زود به IP جدید
   بروند.

6. گواهی‌ها و تنظیم HAProxy را منتقل کنید:

   ~~~bash
   # روی سرور قدیم
   sudo tar czf /home/deploy/tls-certs.tar.gz /etc/letsencrypt /etc/haproxy/certs
   sudo chown deploy:deploy /home/deploy/tls-certs.tar.gz
   scp /home/deploy/tls-certs.tar.gz deploy@NEW_IP:/home/deploy/

   # روی سرور جدید
   sudo tar xzf /home/deploy/tls-certs.tar.gz -C /
   sudo cp /srv/vpn-backend/haproxy.conf /etc/haproxy/haproxy.cfg
   sudo haproxy -c -f /etc/haproxy/haproxy.cfg
   sudo systemctl enable --now haproxy
   rm /home/deploy/tls-certs.tar.gz
   ~~~

   و روی سرور قدیم هم `tls-certs.tar.gz` را پاک کنید؛ کلید خصوصی داخل آن
   است. اگر `haproxy.cfg` سرور قدیم با `haproxy.conf` مخزن فرق دارد، نسخهٔ
   داخل پوشهٔ بکاپ (`haproxy.cfg`) را نصب کنید.

## مرحلهٔ ۱: تمرین

### ۱.۱ بکاپ زنده روی سرور قدیم

~~~bash
cd /srv/vpn-backend
scripts/backup.sh
~~~

هیچ چیزی متوقف نمی‌شود. خروجی، مسیر پوشهٔ بکاپ و محتوای `manifest.txt` را
نشان می‌دهد؛ تعداد رکوردها و فایل‌ها را همان‌جا ببینید تا بعداً با سرور جدید
مقایسه کنید. پوشهٔ بکاپ شامل کپی `.env` است و با مجوز `700` ساخته می‌شود؛
`backups/` در `.gitignore` هست و هرگز commit نمی‌شود.

### ۱.۲ انتقال به سرور جدید

~~~bash
# روی سرور قدیم؛ <ts> را با نام پوشه‌ای که backup.sh ساخت جایگزین کنید
ssh deploy@NEW_IP mkdir -p /srv/vpn-backend/backups
rsync -avz --progress -e ssh backups/<ts> deploy@NEW_IP:/srv/vpn-backend/backups/
~~~

بدون `/` انتهایی بعد از `<ts>`، تا خود پوشه کپی شود نه محتوایش.

### ۱.۳ ساخت `.env` سرور جدید

~~~bash
cd /srv/vpn-backend
cp backups/<ts>/env.backup .env
chmod 600 .env
nano .env
~~~

این مقادیر را بازبینی کنید؛ بقیه بدون تغییر می‌مانند:

- `ALLOWED_HOSTS`: اگر IP سرور قدیم داخلش هست، IP جدید را بگذارید.
- `TELEGRAM_PROXY_URL`: اگر به سرور قدیم اشاره می‌کند (بالا را ببینید).
- `POSTGRES_DB`: روی سرور قدیم به دلیل تاریخی `fitness_db` است (`CLAUDE.md`
  را ببینید). روی سرور جدید volume از صفر ساخته می‌شود، پس **همین‌جا** می‌توانید
  نام تمیزتری مثل `vpn_db` بگذارید؛ `restore.sh` دیتابیس را با نامی که در
  `.env` سرور جدید هست می‌سازد و dump به نام قدیمی وابسته نیست. اگر نمی‌خواهید
  چیزی عوض شود، دست نزنید. بعد از اولین `up` دیگر عوضش نکنید.
- `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD` را **عوض نکنید**. متادیتای مینیو
  با همین‌ها ساخته شده و Django هم با همین‌ها به S3 وصل می‌شود. اگر می‌خواهید
  rotate کنید، بعد از پایان انتقال و با روش خود مینیو.
- `POSTGRES_HOST` و `MINIO_ENDPOINT` نام کانتینرها هستند و عوض نمی‌شوند.

### ۱.۴ بازیابی

~~~bash
cd /srv/vpn-backend
scripts/restore.sh backups/<ts>
~~~

اسکریپت اول checksum فایل‌ها را چک می‌کند، برنامه‌ای که قرار است انجام دهد
را نشان می‌دهد و تأیید می‌گیرد. بعد:

1. فقط `spacedigital_vpn_db` را بالا می‌آورد (اگر image نباشد pull می‌شود).
2. دیتابیس `.env` را می‌سازد و `db.dump` را در یک تراکنش بازیابی می‌کند؛ اگر
   خطایی باشد دیتابیس خالی می‌ماند، نه نصفه.
3. کانتینر و volume مینیو را می‌سازد، آرشیو را داخل volume باز می‌کند و
   مینیو را بالا می‌آورد.
4. تعداد رکوردهای جدول‌های اصلی و تعداد فایل‌های هر bucket را با
   `manifest.txt` مقایسه می‌کند و یک فایل عمومی را بدون امضا از پورت ۹۰۰۰
   می‌خواند تا مطمئن شود policy دانلود عمومی `media` هم منتقل شده.

اگر image مینیویی که روی سرور جدید pull شده با سرور قدیم فرق داشته باشد
هشدار می‌دهد. نسخه‌های جدیدتر مینیو همین فرمت داده را می‌خوانند، ولی اگر
می‌خواهید دقیقاً همان build اجرا شود، خطی که هشدار می‌گوید را در `.env`
بگذارید (`MINIO_IMAGE=minio/minio@sha256:...`) و `restore.sh` را دوباره اجرا
کنید؛ `docker-compose.yml` این متغیر را می‌خواند.

در پایان باید همهٔ سطرها `ok` باشند. برای بکاپ زنده، اختلاف کوچک (چیزی که
حین بکاپ نوشته شده) طبیعی است؛ برای بکاپ نهایی باید همه دقیقاً یکی باشند.

### ۱.۵ بالا آوردن و تست، بدون ربات

~~~bash
docker compose up -d --build spacedigital_vpn_django
docker compose logs -f spacedigital_vpn_django   # تا «Starting Gunicorn»
~~~

**ربات را در تمرین بالا نیاورید.** توکن ربات یکی است و تلگرام به دومین
poller خطای ۴۰۹ می‌دهد؛ یا ربات قدیم از کار می‌افتد یا جدید، بدون هیچ خطای
واضحی. `migrate` روی دیتابیس بازیابی‌شده کاری نمی‌کند چون `django_migrations`
هم منتقل شده.

از لپ‌تاپ خودتان، بدون دست‌زدن به DNS:

~~~bash
curl -sI --resolve api.spacedigital.top:443:NEW_IP https://api.spacedigital.top/admin/login/
curl -sI --resolve minio.spacedigital.top:443:NEW_IP https://minio.spacedigital.top/media/<یک-کلید-از-پنجرهٔ-restore>
~~~

هر دو باید `200` بدهند (اولی ممکن است `302` باشد؛ مهم این است که `502` یا
خطای گواهی نباشد). برای دیدن پنل ادمین در مرورگر، در فایل hosts سیستم
خودتان `NEW_IP api.spacedigital.top minio.spacedigital.top` را موقتاً اضافه
کنید، وارد `/admin/` شوید، یک کاربر و یک فیش را باز کنید و عکس پروفایل و
فایل فیش را ببینید. در این مدت **فیشی را تأیید نکنید**؛ تأیید روی پنل
3x-ui واقعی کلاینت می‌سازد.

بعد از تمرین، سرور جدید را خاموش کنید تا وقت انتقال نهایی برسد:

~~~bash
docker compose stop
~~~

## مرحلهٔ ۲: انتقال نهایی

### ۲.۱ روی سرور قدیم

~~~bash
cd /srv/vpn-backend
scripts/backup.sh --final
~~~

با `--final` اسکریپت قبل از dump، `spacedigital_vpn_django` و
`spacedigital_vpn_telegram_bot` را متوقف می‌کند و restart policy آن‌ها را
`no` می‌کند تا اگر سرور قدیم reboot شد، ربات قدیم دوباره بالا نیاید و با ربات
جدید تداخل نکند. مینیو هم قبل از آرشیو متوقف می‌شود. از این لحظه سرویس
down است؛ بقیهٔ مراحل را پشت سر هم انجام دهید.

~~~bash
rsync -avz --progress -e ssh backups/<ts-final> deploy@NEW_IP:/srv/vpn-backend/backups/
~~~

### ۲.۲ روی سرور جدید

~~~bash
cd /srv/vpn-backend
docker compose stop
diff backups/<ts-final>/env.backup backups/<ts-تمرین>/env.backup   # اگر .env سرور قدیم از زمان تمرین عوض شده، همان تغییر را در .env بدهید
scripts/restore.sh backups/<ts-final> --force
docker compose up -d --build
docker compose logs -f spacedigital_vpn_django          # تا «Starting Gunicorn»
docker compose logs -f spacedigital_vpn_telegram_bot    # باید بدون Conflict پیغام polling بدهد
~~~

`--force` لازم است چون دیتابیس و volume مینیوی سرور جدید از تمرین پر هستند؛
بدون آن اسکریپت با پیغام روشن متوقف می‌شود و چیزی را پاک نمی‌کند.

### ۲.۳ چرخاندن DNS

رکوردهای A زیر را به IP سرور جدید تغییر دهید:

- `api.spacedigital.top`
- `minio.spacedigital.top`
- `api.bodyremix.ir`
- هر دامنهٔ دیگری که در `haproxy.conf` هست و به سرور جدید منتقل شده

انتشار را چک کنید:

~~~bash
dig +short api.spacedigital.top
dig +short minio.spacedigital.top
~~~

### ۲.۴ بررسی بعد از انتقال

- `curl -I https://api.spacedigital.top/admin/login/` از بیرون.
- ورود به پنل ادمین، دیدن عکس پروفایل یک کاربر (bucket عمومی) و فایل یک فیش
  (bucket خصوصی از طریق view دانلود).
- در اپ: ورود، دیدن اشتراک، آپلود یک فیش آزمایشی؛ در ربات: `/start` و
  رسیدن همان فیش به گروه ادمین.
- `docker compose ps` همه `Up` باشند و `docker compose logs --tail=100` بدون
  traceback.
- crontab همگام‌سازی مصرف را از `backups/<ts-final>/crontab.txt` روی سرور
  جدید وارد کنید (`crontab -e`) و روی سرور قدیم حذفش کنید (`crontab -r`).
- `sudo certbot renew --dry-run` روی سرور جدید بعد از این‌که DNS چرخید.
  اگر به‌خاطر اشغال‌بودن پورت ۸۰ توسط HAProxy خطا داد، همان روشی را که روی
  سرور قدیم برای تمدید داشتید تکرار کنید.
- کنسول مینیو (پورت ۹۰۰۱ روی HAProxy) اگر استفاده می‌کنید، در ufw سرور جدید
  باز باشد.

### ۲.۵ بعد از چند روز

سرور قدیم را حداقل یک هفته با همان وضعیت (کانتینرها متوقف، volumeها دست‌نخورده)
نگه دارید. وقتی از سرور جدید مطمئن شدید:

- پوشه‌های `backups/` را از هر دو سرور پاک کنید؛ کپی `.env` داخلشان است. اگر
  می‌خواهید بکاپ نگه دارید، رمزنگاری‌شده و خارج از VPS.
- سرور قدیم را حذف کنید یا اگر می‌ماند `docker compose down` (بدون `-v`).

## بازگشت (rollback)

اگر بعد از انتقال نهایی به مشکلی خوردید که سریع حل نمی‌شود:

~~~bash
# روی سرور جدید: مخصوصاً ربات را خاموش کنید
docker compose stop

# روی سرور قدیم
docker update --restart=always spacedigital_vpn_django spacedigital_vpn_telegram_bot
docker start spacedigital_vpn_minio spacedigital_vpn_django spacedigital_vpn_telegram_bot
~~~

و DNS را به IP قدیم برگردانید. هرچه بعد از انتقال روی سرور جدید نوشته شده
(فیش، ثبت‌نام) با این بازگشت گم می‌شود؛ اگر مهم است، همین اسکریپت‌ها را در جهت
برعکس اجرا کنید (backup روی جدید، restore روی قدیم با `--force`).

## نکته‌های مخصوص این پروژه

- **فقط یک poller تلگرام.** دو کانتینر ربات با یک توکن هم‌زمان کار نمی‌کنند
  و خطایش هم جایی نزدیک دکمه‌ای که از کار افتاده دیده نمی‌شود. به همین
  دلیل `--final` ربات قدیم را `restart=no` می‌کند و در تمرین ربات جدید بالا
  نمی‌آید.
- **`docker compose down -v` هرگز.** `-v` هر دو volume را پاک می‌کند.
- **`CSRF_TRUSTED_ORIGINS` و `AWS_S3_CUSTOM_DOMAIN`** در `config/settings.py`
  hard-code شده‌اند. تا وقتی دامنه‌ها همان‌ها هستند کاری لازم نیست؛ اگر
  دامنه عوض می‌شود، قبل از build روی سرور جدید آن دو خط را هم عوض کنید.
- **image مینیو** در compose به tag ثابتی pin نشده. `backup.sh` digest نسخهٔ
  در حال اجرا را در `manifest.txt` می‌نویسد و `restore.sh` اگر فرق کند هشدار
  می‌دهد؛ با `MINIO_IMAGE` در `.env` می‌توانید همان را اجرا کنید.
- **postgres:15** روی هر دو سرور باید هم‌نسخه (همان major) باشد؛ compose
  همین را pin کرده. dump با `pg_restore` نسخهٔ جدیدتر همان major مشکلی
  ندارد.
- **پوشهٔ بکاپ حاوی رمز است.** با مجوز `700` ساخته می‌شود، فقط با SSH
  جابه‌جا شود و بعد از کار پاک شود.

## عیب‌یابی

- **`restore.sh` می‌گوید دیتابیس جدول دارد / volume فایل دارد.** یعنی قبلاً
  چیزی روی سرور جدید بالا آمده (تمرین یا یک `up` زودهنگام). اگر مطمئنید
  می‌خواهید روی آن بنویسید، `--force`.
- **`restore.sh` می‌گوید بکاپ روی همین سرور گرفته شده.** اسکریپت را روی سرور
  اشتباه اجرا کرده‌اید؛ روی سرور قدیم اجرا نکنید.
- **checksum mismatch.** `rsync` را دوباره اجرا کنید؛ فایلی ناقص کپی شده.
- **`pg_restore` خطا می‌دهد.** دیتابیس خالی مانده و می‌توانید دوباره تلاش
  کنید. متن خطا را بخوانید؛ معمولاً اختلاف نسخهٔ postgres است (`manifest.txt`،
  کلید `postgres_server_version`). برای دیدن همهٔ خطاها به‌جای اولی، همان دستور
  را بدون `--single-transaction --exit-on-error` دستی اجرا کنید:
  `docker exec -i spacedigital_vpn_db pg_restore -U <user> -d <db> --no-owner --no-acl < backups/<ts>/db.dump`
- **عکس پروفایل ۴۰۳ می‌دهد** (یا `restore.sh` در بررسی آخر هشدار داد). policy
  عمومی bucket منتقل نشده؛ دوباره تنظیمش کنید:

  ~~~bash
  cd /srv/vpn-backend
  docker run --rm --network spacedigital_vpn_net \
    -e MINIO_ROOT_USER="$(docker exec spacedigital_vpn_minio printenv MINIO_ROOT_USER)" \
    -e MINIO_ROOT_PASSWORD="$(docker exec spacedigital_vpn_minio printenv MINIO_ROOT_PASSWORD)" \
    --entrypoint /bin/sh minio/mc -c '
    mc alias set local http://spacedigital_vpn_minio:9000 "$MINIO_ROOT_USER" "$MINIO_ROOT_PASSWORD" &&
    mc anonymous set download local/media &&
    mc anonymous set none local/private &&
    mc anonymous get local/media'
  ~~~

  رمزها از env خود کانتینر مینیو خوانده می‌شوند تا دقیقاً همان مقداری باشند
  که compose به آن داده (نه `.env` خام، که `docker run --env-file` کوتیشن‌هایش
  را برنمی‌دارد).

- **ربات جواب نمی‌دهد و در لاگش `Conflict` یا `409` هست.** ربات قدیم هنوز
  در حال polling است؛ روی سرور قدیم `docker stop spacedigital_vpn_telegram_bot`.
- **`database "..." does not exist` در لاگ django.** `POSTGRES_DB` در `.env` با
  دیتابیسی که ساخته شده فرق دارد. روی سرور جدید `restore.sh` را دوباره اجرا
  کنید؛ دیتابیس را با نام فعلی `.env` می‌سازد.
- **`docker cp` حین بکاپ زنده خطای «file changed» می‌دهد.** کسی همان لحظه
  فایل آپلود کرده؛ دوباره اجرا کنید یا برای بکاپ نهایی از `--final` استفاده
  کنید که مینیو را متوقف می‌کند.
