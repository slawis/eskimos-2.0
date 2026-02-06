========================================================
     ESKIMOS SMS GATEWAY v2.6.0
     Instalacja i konfiguracja
========================================================


WYMAGANIA
---------
- Windows 10/11 (64-bit)
- Modem USB (Alcatel/TCL) podlaczony przez RNDIS
- Dostep do internetu (heartbeat + zdalne aktualizacje)


SZYBKA INSTALACJA (zainstaluj i zapomnij)
------------------------------------------

1. Rozpakuj EskimosGateway.zip do C:\EskimosGateway\

2. Edytuj config\.env - ustaw numer telefonu modemu:
   ESKIMOS_MODEM_PHONE=886480453

3. Uruchom INSTALL_SERVICE.bat jako Administrator
   (kliknij prawym przyciskiem -> "Uruchom jako administrator")

4. Gotowe! Serwisy uruchomia sie automatycznie po kazdym
   starcie Windows. Nie trzeba nic wiecej robic.


CO SIE INSTALUJE
----------------

Dwa serwisy Windows (auto-start):

  EskimosGateway  - Glowna aplikacja SMS (API + Dashboard)
                    http://localhost:8000/dashboard

  EskimosDaemon   - Daemon phone-home:
                    - Heartbeat co 60 sekund
                    - Zdalne komendy (restart, update, diagnostic)
                    - Automatyczne aktualizacje
                    - Wykrywanie modemu TCL/Alcatel


STRUKTURA PLIKOW
-----------------

  EskimosGateway\
  ├── python\              Python 3.11 (embedded)
  ├── chromium\            Chrome for Testing
  ├── eskimos\             Kod zrodlowy aplikacji
  ├── tools\
  │   └── nssm.exe         Service manager
  ├── config\
  │   └── .env             Konfiguracja
  ├── logs\                Logi serwisow (tworzony automatycznie)
  │
  ├── INSTALL_SERVICE.bat  Instalacja serwisow (1 raz)
  ├── UNINSTALL_SERVICE.bat  Deinstalacja serwisow
  ├── SERVICE_STATUS.bat   Sprawdz status
  ├── SERVICE_START.bat    Uruchom serwisy
  ├── SERVICE_STOP.bat     Zatrzymaj serwisy
  │
  ├── START_ALL.bat        Reczne uruchomienie (bez serwisow)
  ├── START.bat            Tylko Gateway (konsola)
  ├── START_DASHBOARD.bat  Gateway + otworz przegladarke
  ├── DAEMON.bat           Tylko Daemon (konsola)
  ├── STOP_ALL.bat         Zatrzymaj wszystko
  ├── STOP.bat             Zatrzymaj Gateway
  │
  ├── CONFIG.bat           Edytuj konfiguracje
  ├── UPDATE.bat           Reczna aktualizacja (normalnie automatyczna)
  └── README.txt           Ten plik


KONFIGURACJA (.env)
-------------------

  ESKIMOS_MODEM_HOST=192.168.1.1     IP modemu (domyslnie OK)
  ESKIMOS_MODEM_PHONE=886480453      Numer telefonu w modemie
  ESKIMOS_DEBUG=false                 Tryb debugowania


ZARZADZANIE SERWISAMI
---------------------

  Przez skrypty BAT:
    SERVICE_STATUS.bat    - pokaz status
    SERVICE_STOP.bat      - zatrzymaj
    SERVICE_START.bat     - uruchom
    UNINSTALL_SERVICE.bat - calkowicie usun

  Przez Windows Services (services.msc):
    - Eskimos SMS Gateway
    - Eskimos Phone-Home Daemon

  Logi:
    logs\gateway_service.log   - logi Gateway
    logs\gateway_error.log     - bledy Gateway
    logs\daemon_service.log    - logi Daemon
    logs\daemon_error.log      - bledy Daemon


ZDALNE ZARZADZANIE
------------------

  Dashboard centralny: https://app.ninjabot.pl/eskimos/

  Dostepne komendy zdalne:
    - restart          Restart calego laptopa
    - restart_gateway  Restart serwisu Gateway
    - update           Automatyczna aktualizacja kodu
    - diagnostic       Diagnostyka (modem, system, wersja)


ROZWIAZYWANIE PROBLEMOW
------------------------

  1. Serwis sie nie uruchamia:
     - Sprawdz logi w folderze logs\
     - Uruchom SERVICE_STATUS.bat
     - Sprobuj UNINSTALL_SERVICE.bat i ponowna instalacje

  2. Modem nie wykryty:
     - Sprawdz czy modem jest podlaczony przez USB
     - Otworz http://192.168.1.1 w przegladarce
     - Sprawdz czy ESKIMOS_MODEM_HOST w .env jest poprawny

  3. Brak polaczenia z centralnym serwerem:
     - Sprawdz dostep do internetu
     - Logi daemon_error.log pokaza bledy polaczenia

  4. Reczne uruchomienie (bez serwisow):
     - Uruchom START_ALL.bat (Gateway + Daemon w konsolach)


========================================================
  Eskimos SMS Gateway v2.6.0
  NinjaBot Team | 2026
========================================================
