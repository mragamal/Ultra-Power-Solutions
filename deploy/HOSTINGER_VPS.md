# Premium One ERP on Hostinger VPS

Use these commands on a fresh Ubuntu VPS. Replace `your-domain.com` and the GitHub URL if needed.

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-venv python3-pip git nginx

sudo adduser --system --group --home /var/www/premium-one-erp erp
sudo mkdir -p /var/www/premium-one-erp
sudo chown -R erp:www-data /var/www/premium-one-erp

sudo -u erp git clone https://github.com/mragamal/Ultra-Power-Solutions.git /var/www/premium-one-erp
cd /var/www/premium-one-erp

sudo -u erp python3 -m venv venv
sudo -u erp ./venv/bin/pip install --upgrade pip
sudo -u erp ./venv/bin/pip install -r requirements.txt

sudo -u erp mkdir -p data uploads
sudo chown -R erp:www-data /var/www/premium-one-erp

sudo cp deploy/premium-one-erp.service /etc/systemd/system/premium-one-erp.service
sudo nano /etc/systemd/system/premium-one-erp.service

sudo systemctl daemon-reload
sudo systemctl enable premium-one-erp
sudo systemctl start premium-one-erp
sudo systemctl status premium-one-erp

sudo cp deploy/nginx-premium-one-erp.conf /etc/nginx/sites-available/premium-one-erp
sudo nano /etc/nginx/sites-available/premium-one-erp
sudo ln -s /etc/nginx/sites-available/premium-one-erp /etc/nginx/sites-enabled/premium-one-erp
sudo nginx -t
sudo systemctl reload nginx
```

For SSL:

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com -d www.your-domain.com
```

Update after a new GitHub push:

```bash
cd /var/www/premium-one-erp
sudo -u erp git pull
sudo -u erp ./venv/bin/pip install -r requirements.txt
sudo systemctl restart premium-one-erp
```
