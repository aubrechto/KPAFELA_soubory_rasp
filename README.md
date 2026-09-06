# KPAFELA_soubory_rasp

## Lokální NTP server pro ESP32

Raspberry Pi lze použít jako NTP server pro ESP32 zařízení připojená do stejné Wi-Fi sítě. Skript používá `chrony`, synchronizuje Raspberry Pi s internetovými časovými servery a povolí NTP dotazy pouze z lokálního subnetu Wi-Fi.

Na Raspberry Pi spusť:

```bash
cd /home/admin/KPAFELA_soubory_rasp
sudo bash scripts/setup_ntp_server.sh
```

Pokud Wi-Fi rozhraní nebo síť nejdou automaticky zjistit, lze je zadat explicitně:

```bash
sudo WIFI_INTERFACE=wlan0 WIFI_CIDR=192.168.1.0/24 bash scripts/setup_ntp_server.sh
```

Adresa Raspberry Pi vypsaná skriptem je NTP adresa pro ESP32. ESP32 musí používat UDP port `123` a jako NTP hostname/IP nastavit tuto adresu, ne `pool.ntp.org`. Ověření na Raspberry Pi:

```bash
chronyc tracking
chronyc sources -v
sudo ss -lunp | grep ':123'
```

Skript je určen pro Raspberry Pi OS/Debian a vyžaduje připojení k internetu při instalaci, aby mohl Pi nejprve synchronizovat vlastní čas.

## Wi-Fi access point pro ESP

Raspberry Pi může vytvořit vlastní Wi-Fi síť pro ESP, DHCP server a MQTT broker.
AP je záměrně manuální: po rebootu se nespustí automaticky, takže Raspberry
můžeš spravovat přes SSH v běžné síti. Při prvním nastavení spusť:

```bash
cd /home/admin/KPAFELA_soubory_rasp
sudo AP_SSID=KAPFELA-ESP AP_PASSWORD='kapfela-esp-1234' bash scripts/setup_wifi_ap.sh
```

AP se po instalaci spustí ihned, ale po dalším rebootu zůstane vypnutý. Ruční
spuštění a zastavení:

```bash
sudo bash scripts/start_wifi_ap.sh
sudo bash scripts/stop_wifi_ap.sh
```

Pro SSH po rebootu připoj Raspberry přes Ethernet nebo jiné síťové rozhraní k
běžné síti. Pokud používáš stejné `wlan0` jako AP, nemůže být současně běžným
Wi-Fi klientem domácí sítě; v takovém případě použij Ethernet nebo druhý Wi-Fi
adaptér.

Výchozí síť používá adresu Raspberry `192.168.50.1`, DHCP rozsah
`192.168.50.50-192.168.50.150`, MQTT broker na `192.168.50.1:1883` a dashboard
na `http://192.168.50.1:8000`. ESP musí používat stejné SSID/heslo a jako MQTT
hostitele adresu `192.168.50.1`.

Skript automaticky použije NetworkManager, pokud je na Raspberry dostupný;
na starším Raspberry Pi OS použije `hostapd` a `dnsmasq`. Stav ověříš:

```bash
nmcli connection show --active
systemctl status mosquitto
ip address show wlan0
```

Podrobnou diagnostiku AP spustíš:

```bash
sudo bash scripts/check_wifi_ap.sh
```

Před testem musí být ESP nastavené přesně na stejné hodnoty:

```text
SSID: KAPFELA-ESP
heslo: kapfela-esp-1234
MQTT: 192.168.50.1:1883
```

Pokud byl AP skript spuštěn s vlastním `AP_PASSWORD`, musí být stejné heslo
zapsané také v ESP firmware. Hláška `SSID not found` znamená, že AP nevysílá,
je vypnuté rádio, nebo ESP hledá jiné SSID; v takovém případě nejdřív spusť
`check_wifi_ap.sh`.

## Knihovna skladeb

Zdrojové MuseScore soubory jsou ve složce `songs`. Konvertor vytvoří pro každou
skladbu jeden MessagePack soubor se stopami `guitar`, `bass` a `drums`:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python tools/generate_songs.py -o Data/songs
python tools/sync_playlist.py
```

Výstupy jsou v `Data/songs` a playlist v `Data/playlist.json`. Do playlistu se
zařadí pouze skladby, které mají současně metadata `.json` i hotový `.msg` soubor.

## Kompletní převod a upload na ESP

Po připojení Raspberry k MQTT síti spusť jeden příkaz:

```bash
python scripts/convert_and_upload.py
```

Skript postupně převede skladby, obnoví playlist, připojí se k MQTT a každou
hotovou skladbu odešle na `guitar`, `bass` a `drums`. Po každém uploadu čeká na
potvrzení `upload_finished` z ESP; při `upload_error` nebo timeoutu skončí s
chybou.

Pokud už jsou skladby převedené a chceš pouze opakovat upload:

```bash
python scripts/convert_and_upload.py --skip-convert
```

První test můžeš omezit jen na jedno zařízení:

```bash
python scripts/convert_and_upload.py --instruments guitar
```

Parametry pro jinou síť:

```bash
python scripts/convert_and_upload.py --mqtt-host 192.168.50.1 --mqtt-port 1883
```