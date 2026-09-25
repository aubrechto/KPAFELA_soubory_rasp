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

### Čas bez internetu

Chrony je nastavené s `local stratum 10`, takže Pi rozdává svůj čas ESP zařízením i když samo není synchronizované z internetu. Když otevřeš dashboard v prohlížeči (notebook, tablet), stránka automaticky pošle svůj čas na `/api/time/sync` a Pi podle něj nastaví systémové hodiny (max. 1x za 5 minut a jen při odchylce větší než 2 s). Aktuální čas Pi je vidět v hlavičce stránky Player.

Nastavení hodin vyžaduje sudoers pravidlo, které setup skript vytvoří v `/etc/sudoers.d/kapfela-time` pro uživatele `admin` (jiného uživatele nastav přes `KAPFELA_USER`).

### Ověření synchronizace času na ESP

Každá ESP deska se po připojení k Wi-Fi nejprve zkusí synchronizovat čas přes NTP z Raspberry Pi (`192.168.50.1`). Jakmile ESP publikuje status s `ntp_synced: false` (tedy je právě připojené a ještě nemá platný čas), backend mu rovnou pošle vlastní čas Raspberry Pi přes MQTT topic `kapfela/instrument/<nastroj>/time` (payload `{"epoch": <unix čas>}`) a ESP si podle něj nastaví hodiny přímo, místo aby čekalo na NTP.

Kromě toho si ESP hned po připojení a pak každou minutu samo vyžádá přesnější
resync: pošle `kapfela/instrument/<nastroj>/time_request` s vlastním `t0_ms`
(`millis()`), backend okamžitě odpoví na `.../time` s `epoch` a stejným
`t0_ms` zpět. ESP z rozdílu `millis() - t0_ms` spočítá round-trip a půlku
zpoždění přičte k epoše, takže se kompenzuje síťové/MQTT zpoždění místo
naivního "co přijde, to nastavím". Pravidelné opakování navíc opravuje
přirozený drift krystalu ESP32 (řádově desetiny sekundy za hodinu), takže
odchylka na dashboardu zůstává trvale nízká, ne jen hned po startu.

ESP svůj aktuální čas posílá zpět v MQTT status zprávě (`ntp_time`). Dashboard u každého instrumentu zobrazuje jeho živý čas; pokud se odchylka vůči Raspberry Pi překročí 0,5 s, čas se zvýrazní oranžově a tooltip ukáže přesnou odchylku. Backend odchylku počítá z `ntp_time` kompenzovaného o stáří poslední status zprávy.

## Wi-Fi access point pro ESP

Raspberry Pi může vytvořit vlastní Wi-Fi síť pro ESP, DHCP server a MQTT broker.
AP je záměrně manuální: po rebootu se nespustí automaticky, takže Raspberry
můžeš spravovat přes SSH v běžné síti. Při prvním nastavení spusť:

```bash
cd /home/admin/KPAFELA_soubory_rasp
sudo AP_SSID=KAPFELA-ESP AP_PASSWORD='kapfela-esp-1234' bash scripts/setup_wifi_ap.sh
```

AP se po instalaci spustí ihned, ale po dalším rebootu zůstane vypnutý (pokud
nezapneš autostart, viz níže). Ruční spuštění a zastavení:

```bash
sudo bash scripts/start_wifi_ap.sh
sudo bash scripts/stop_wifi_ap.sh
```

`stop_wifi_ap.sh` je vhodný, když se potřebuješ dočasně připojit k běžné síti
(např. kvůli aktualizaci) - jde jen o aktuální relaci. Po zapnutém autostartu
(viz níže) se AP po každém rebootu spustí znovu bez ohledu na to, k jaké síti
byl `wlan0` naposledy připojený.

### Automatické spuštění AP po bootu

Pokud chceš, aby se AP zapnul hned po startu Raspberry Pi, spusť jednou:

```bash
sudo bash scripts/enable_wifi_ap_autostart.sh
```

Skript pozná používaný backend: s NetworkManagerem nastaví `connection.autoconnect yes` a nejvyšší prioritu na spojení `kapfela-ap` a navíc založí službu `kapfela-ap.service`, která po každém rebootu AP vynutí zapnuté - i když jsi mezitím přes `stop_wifi_ap.sh` přepnul `wlan0` na běžnou síť. Na starším systému povolí služby `hostapd` a `dnsmasq`. Vrácení na manuální režim:

```bash
sudo systemctl disable --now kapfela-ap.service
sudo nmcli connection modify kapfela-ap connection.autoconnect no
# nebo na starším systému:
sudo systemctl disable hostapd dnsmasq
```

Pro SSH po rebootu připoj Raspberry přes Ethernet nebo jiné síťové rozhraní k
běžné síti. Pokud používáš stejné `wlan0` jako AP, nemůže být současně běžným
Wi-Fi klientem domácí sítě; v takovém případě použij Ethernet nebo druhý Wi-Fi
adaptér.

Výchozí síť používá adresu Raspberry `192.168.50.1`, DHCP rozsah
`192.168.50.50-192.168.50.150`, MQTT broker na `192.168.50.1:1883` a dashboard
na `http://192.168.50.1:8000`. AP používá 2,4 GHz, režim `bg` a kanál 6.
ESP musí používat stejné SSID/heslo a jako MQTT hostitele adresu `192.168.50.1`.

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