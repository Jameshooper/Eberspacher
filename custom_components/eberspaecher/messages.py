"""Texts the integration sends or shows itself, in the language Home Assistant uses.

Home Assistant's translation files only cover what it displays on its own (menus,
entity names, notices). The notifications and the schedule's last result are built
here, so they are looked up by language with English as the fallback.
"""

from __future__ import annotations

from homeassistant.core import HomeAssistant

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "started_title_heating": "Car heating started",
        "started_title_ventilation": "Car ventilation started",
        "started_body_heating": "Heating for {minutes} min, ready by {ready}. {reading}",
        "started_body_ventilation": "Ventilation for {minutes} min, ready by {ready}. {reading}",
        "reading_sensor": "{temp} °C from {source}.",
        "reading_heater": "{temp} °C from the heater's last reading.",
        "result_started_heating": "Heating {minutes} min at {time}",
        "result_started_ventilation": "Ventilation {minutes} min at {time}",
        "failed_title": "Car heater failed to start",
        "failed_body": "{reason}. Start it from the EasyStart app or the car.",
        "result_failed": "Failed: {reason}",
        "no_reading_title": "Car heater not started",
        "no_reading_body": "There is no temperature reading, so the schedule did not "
        "start the heater. Check the temperature sensor.",
        "result_no_reading": "Not started: no temperature reading",
        "not_needed_title": "Car heater not needed today",
        "not_needed_body": "It is {temp} °C, so the heater was not started.",
        "result_not_needed": "Not needed ({temp} °C)",
        "idle_title": "Car heater has not run for a while",
        "idle_body": "The car heater has not run for {days} days. Run it for about 10 "
        "minutes so the fuel lines and pump stay in working order.",
        "prefix_work": "At work: ",
        "result_skipped_home": "Skipped: you were not at home",
        "result_skipped_work": "Skipped: your phone is at home",
        "plan_title": "Car ready time",
        "plan_body": "Tomorrow the car will be ready by {time}, learned from your recent departures.",
    },
    "de": {
        "started_title_heating": "Auto-Heizung gestartet",
        "started_title_ventilation": "Auto-Lüftung gestartet",
        "started_body_heating": "Heizen für {minutes} Min., fertig um {ready}. {reading}",
        "started_body_ventilation": "Lüften für {minutes} Min., fertig um {ready}. {reading}",
        "reading_sensor": "{temp} °C von {source}.",
        "reading_heater": "{temp} °C laut letzter Messung der Standheizung.",
        "result_started_heating": "Heizen {minutes} Min. um {time}",
        "result_started_ventilation": "Lüften {minutes} Min. um {time}",
        "failed_title": "Standheizung konnte nicht gestartet werden",
        "failed_body": "{reason}. Starte sie über die EasyStart-App oder am Auto.",
        "result_failed": "Fehlgeschlagen: {reason}",
        "no_reading_title": "Standheizung nicht gestartet",
        "no_reading_body": "Es gibt keinen Temperaturwert, daher hat der Zeitplan die "
        "Standheizung nicht gestartet. Prüfe den Temperatursensor.",
        "result_no_reading": "Nicht gestartet: kein Temperaturwert",
        "not_needed_title": "Standheizung heute nicht nötig",
        "not_needed_body": "Es sind {temp} °C, daher wurde die Standheizung nicht gestartet.",
        "result_not_needed": "Nicht nötig ({temp} °C)",
        "idle_title": "Standheizung lief längere Zeit nicht",
        "idle_body": "Die Standheizung lief seit {days} Tagen nicht. Lass sie etwa 10 "
        "Minuten laufen, damit Kraftstoffleitungen und Pumpe in Ordnung bleiben.",
        "prefix_work": "Bei der Arbeit: ",
        "result_skipped_home": "Übersprungen: du warst nicht zu Hause",
        "result_skipped_work": "Übersprungen: dein Handy ist zu Hause",
        "plan_title": "Bereitzeit des Autos",
        "plan_body": "Morgen ist das Auto um {time} fertig, gelernt aus deinen letzten Abfahrten.",
    },
    "sv": {
        "started_title_heating": "Bilvärmen startad",
        "started_title_ventilation": "Bilventilationen startad",
        "started_body_heating": "Värme i {minutes} min, klar till {ready}. {reading}",
        "started_body_ventilation": "Ventilation i {minutes} min, klar till {ready}. {reading}",
        "reading_sensor": "{temp} °C från {source}.",
        "reading_heater": "{temp} °C enligt värmarens senaste avläsning.",
        "result_started_heating": "Värme {minutes} min kl. {time}",
        "result_started_ventilation": "Ventilation {minutes} min kl. {time}",
        "failed_title": "Bilvärmaren kunde inte startas",
        "failed_body": "{reason}. Starta den från EasyStart-appen eller vid bilen.",
        "result_failed": "Misslyckades: {reason}",
        "no_reading_title": "Bilvärmaren startades inte",
        "no_reading_body": "Det finns ingen temperaturavläsning, så schemat startade inte "
        "värmaren. Kontrollera temperatursensorn.",
        "result_no_reading": "Inte startad: ingen temperaturavläsning",
        "not_needed_title": "Bilvärmaren behövs inte i dag",
        "not_needed_body": "Det är {temp} °C, så värmaren startades inte.",
        "result_not_needed": "Behövs inte ({temp} °C)",
        "idle_title": "Bilvärmaren har inte körts på ett tag",
        "idle_body": "Bilvärmaren har inte körts på {days} dagar. Kör den i ungefär 10 "
        "minuter så att bränsleledningar och pump hålls i gott skick.",
        "prefix_work": "På jobbet: ",
        "result_skipped_home": "Hoppade över: du var inte hemma",
        "result_skipped_work": "Hoppade över: din telefon är hemma",
        "plan_title": "Bilens klartid",
        "plan_body": "I morgon är bilen klar till {time}, inlärt från dina senaste avfärder.",
    },
    "fi": {
        "started_title_heating": "Auton lämmitys käynnistetty",
        "started_title_ventilation": "Auton tuuletus käynnistetty",
        "started_body_heating": "Lämmitys {minutes} min, valmis klo {ready}. {reading}",
        "started_body_ventilation": "Tuuletus {minutes} min, valmis klo {ready}. {reading}",
        "reading_sensor": "{temp} °C lähteestä {source}.",
        "reading_heater": "{temp} °C lämmittimen viimeisimmästä lukemasta.",
        "result_started_heating": "Lämmitys {minutes} min klo {time}",
        "result_started_ventilation": "Tuuletus {minutes} min klo {time}",
        "failed_title": "Autolämmittimen käynnistys epäonnistui",
        "failed_body": "{reason}. Käynnistä se EasyStart-sovelluksesta tai autolta.",
        "result_failed": "Epäonnistui: {reason}",
        "no_reading_title": "Autolämmitintä ei käynnistetty",
        "no_reading_body": "Lämpötilalukemaa ei ole, joten aikataulu ei käynnistänyt "
        "lämmitintä. Tarkista lämpötila-anturi.",
        "result_no_reading": "Ei käynnistetty: ei lämpötilalukemaa",
        "not_needed_title": "Autolämmitintä ei tarvita tänään",
        "not_needed_body": "Lämpötila on {temp} °C, joten lämmitintä ei käynnistetty.",
        "result_not_needed": "Ei tarvita ({temp} °C)",
        "idle_title": "Autolämmitin ei ole käynyt pitkään aikaan",
        "idle_body": "Autolämmitin ei ole käynyt {days} päivään. Käytä sitä noin 10 "
        "minuuttia, jotta polttoaineletkut ja pumppu pysyvät kunnossa.",
        "prefix_work": "Töissä: ",
        "result_skipped_home": "Ohitettu: et ollut kotona",
        "result_skipped_work": "Ohitettu: puhelimesi on kotona",
        "plan_title": "Auton valmistumisaika",
        "plan_body": "Huomenna auto on valmis klo {time}, opittu viimeaikaisista lähdöistäsi.",
    },
}


def text(hass: HomeAssistant, key: str, **values: object) -> str:
    """The message for a key in Home Assistant's language (English if not translated)."""
    language = (hass.config.language or "en").split("-")[0].lower()
    template = MESSAGES.get(language, {}).get(key) or MESSAGES["en"][key]
    return template.format(**values)
