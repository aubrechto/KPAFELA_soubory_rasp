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