# datova_schranka
-stahuje všechny zprávy, doručené a odeslané, z datové schránky do souborů ZFO (stahuje pouze ještě nestažené zprávy)


-uživatelské jméno a heslo do DS bere z proměnných prostředí ISDS_USERNAME a ISDS_PASSWORD; příklad nastavení na windows:

set ISDS_USERNAME=SemDejLogin

set ISDS_PASSWORD=SemDejHeslo


-testováno s Python 3.14.4


-instalace vyžadovaných Python modulů pomocí "pip install zeep requests lxml"


-loguje do souboru datova_schranka.log, soubor je nutné ručně přeuložit s kódováním UTF-8
