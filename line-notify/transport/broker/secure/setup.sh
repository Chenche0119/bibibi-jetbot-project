#!/usr/bin/env bash
# 產生本地加密 broker 需要的自簽憑證與密碼檔（只需執行一次）。
#
#   帳密：讀環境變數 JETBOT_MQTT_USERNAME、JETBOT_MQTT_PASSWORD；
#         沒設定時帳號用 jetbot、密碼隨機產生，連同用戶端設定寫進 test.env（已被 .gitignore 忽略）
#   憑證：certs/ca.crt（給用戶端驗證用）、certs/server.crt／server.key（broker 用，主機名稱 localhost）
#
# 產生的檔案都不進版控；刪掉 certs/、passwd、test.env 後重跑即可重新產生。
set -euo pipefail
cd "$(dirname "$0")"

USERNAME="${JETBOT_MQTT_USERNAME:-jetbot}"
PASSWORD="${JETBOT_MQTT_PASSWORD:-$(openssl rand -hex 16)}"

mkdir -p certs
if [ ! -f certs/ca.crt ]; then
  openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=jetbot-test-ca" \
    -keyout certs/ca.key -out certs/ca.crt 2>/dev/null
  openssl req -newkey rsa:2048 -nodes -subj "/CN=localhost" \
    -keyout certs/server.key -out certs/server.csr 2>/dev/null
  printf "subjectAltName=DNS:localhost,IP:127.0.0.1\n" > certs/san.ext
  openssl x509 -req -in certs/server.csr -CA certs/ca.crt -CAkey certs/ca.key -CAcreateserial \
    -days 3650 -extfile certs/san.ext -out certs/server.crt 2>/dev/null
  rm -f certs/server.csr certs/san.ext
  chmod 600 certs/ca.key certs/server.key
  echo "已產生自簽憑證：certs/ca.crt、certs/server.crt"
fi

# 用 broker 映像內附的 mosquitto_passwd 產生雜湊過的密碼檔，Mac 不用安裝 mosquitto
: > passwd
chmod 600 passwd      # Mosquitto 2.x 會警告其他人可讀的密碼檔，之後的版本會拒絕載入
docker run --rm -v "$PWD/passwd:/passwd" eclipse-mosquitto:2 \
  mosquitto_passwd -b /passwd "$USERNAME" "$PASSWORD"
echo "已產生密碼檔 passwd（帳號 $USERNAME）"

if [ -z "${JETBOT_MQTT_PASSWORD:-}" ]; then
  umask 077            # test.env 含密碼，只有自己可讀
  cat > test.env <<ENV
# setup.sh 產生的本地加密 broker 用戶端設定（不進版控）
JETBOT_MQTT_HOST=localhost
JETBOT_MQTT_PORT=8883
JETBOT_MQTT_TLS=1
JETBOT_MQTT_CA_CERTS=$PWD/certs/ca.crt
JETBOT_MQTT_USERNAME=$USERNAME
JETBOT_MQTT_PASSWORD=$PASSWORD
ENV
  echo "密碼為隨機產生，用戶端設定已寫入 test.env"
fi
