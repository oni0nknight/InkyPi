from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image
import os
import requests
import logging
from datetime import datetime, timezone
import pytz
from io import BytesIO
import math
import locale

locale.setlocale(locale.LC_TIME, 'fr_FR.UTF-8')

logger = logging.getLogger(__name__)

UNITS = {
    "standard": {
        "temperature": "K",
        "speed": "m/s"
    },
    "metric": {
        "temperature": "°C",
        "speed": "m/s"

    },
    "imperial": {
        "temperature": "°F",
        "speed": "mph"
    }
}

WEATHER_URL = "https://api.openweathermap.org/data/3.0/onecall?lat={lat}&lon={long}&units={units}&exclude=minutely&appid={api_key}"

AIR_QUALITY_URL = "http://api.openweathermap.org/data/2.5/air_pollution?lat={lat}&lon={long}&appid={api_key}"
GEOCODING_URL = "http://api.openweathermap.org/geo/1.0/reverse?lat={lat}&lon={long}&limit=1&appid={api_key}"
FORECAST_URL = "http://api.openweathermap.org/data/2.5/forecast?lat={lat}&lon={long}&appid={api_key}&lang=fr&units={units}"

class Weather(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['api_key'] = {
            "required": True,
            "service": "OpenWeatherMap",
            "expected_key": "OPEN_WEATHER_MAP_SECRET",
        }
        template_params['style_settings'] = True

        return template_params

    def generate_image(self, settings, device_config):
        api_key = device_config.load_env_key("OPEN_WEATHER_MAP_SECRET")
        if not api_key:
            raise RuntimeError("Open Weather Map API Key not configured.")

        lat = settings.get('latitude')
        long = settings.get('longitude')
        if not lat or not long:
            raise RuntimeError("Latitude and Longitude are required.")

        units = settings.get('units')
        if not units or units not in ['metric', 'imperial', 'standard']:
            raise RuntimeError("Units are required.")

        try:
            weather_data = self.get_weather_data(api_key, units, lat, long)
            aqi_data = self.get_air_quality(api_key, lat, long)
            location_data = self.get_location(api_key, lat, long)
        except Exception as e:
            logger.error(f"Failed to make OpenWeatherMap request: {str(e)}")
            raise RuntimeError("OpenWeatherMap request failure, please check logs.")

        dimensions = device_config.get_resolution()
        if device_config.get_config("orientation") == "vertical":
            dimensions = dimensions[::-1]

        timezone = device_config.get_config("timezone", default="Europe/Paris")
        time_format = device_config.get_config("time_format", default="24h")
        tz = pytz.timezone(timezone)
        template_params = self.parse_weather_data(weather_data, aqi_data, location_data, tz, units, time_format)
        template_params["plugin_settings"] = settings

        # Add last refresh time
        now = datetime.now(tz)
        if time_format == "24h":
            last_refresh_time = now.strftime("%d/%m/%Y %H:%M")
        else:
            last_refresh_time = now.strftime("%d/%m/%Y %I:%M %p")
        template_params["last_refresh_time"] = last_refresh_time

        image = self.render_image(dimensions, "weather.html", "weather.css", template_params)

        if not image:
            raise RuntimeError("Failed to take screenshot, please check logs.")
        return image

    def parse_weather_data(self, weather_data, aqi_data, location_data, tz, units, time_format):
        current = weather_data.get("list")[0]
        daily_forecasts = [
            r for r in weather_data.get("list")[1:]
            if self.get_local_hour(r["dt"], tz) in [12, 13, 14]
        ]

        dt = datetime.fromtimestamp(current.get('dt'), tz=timezone.utc).astimezone(tz)
        current_icon = current.get("weather")[0].get("icon").replace("n", "d")
        location_str = f"{location_data.get('name')}, {location_data.get('state', location_data.get('country'))}"
        data = {
            "current_date": dt.strftime("%A %d %B"),
            "location": location_str,
            "current_day_icon": self.get_plugin_dir(f'icons/{current_icon}.png'),
            "current_temperature": str(round(current.get("main").get("temp"))),
            "feels_like": str(round(current.get("main").get("feels_like"))),
            "temperature_unit": UNITS[units]["temperature"],
            "units": units,
            "time_format": time_format
        }
        data['forecast'] = self.parse_forecast(daily_forecasts, tz)
        data['data_points'] = self.parse_data_points(weather_data, aqi_data, tz, units, time_format)

        data['hourly_forecast'] = self.parse_hourly(weather_data.get("list"), tz, time_format)
        return data

    def parse_forecast(self, daily_forecast, tz):
        """
        - daily_forecast: list of daily entries from One‑Call v3 (each has 'dt', 'weather', 'temp', 'moon_phase')
        - tz: your target tzinfo (e.g. from zoneinfo or pytz)
        """

        forecast = []
        # skip today (i=0)
        for day in daily_forecast:
            # --- weather icon ---
            weather_icon = day["weather"][0]["icon"]  # e.g. "10d", "01n"
            # always show day‑style icon
            weather_icon = weather_icon.replace("n", "d")
            weather_icon_path = self.get_plugin_dir(f"icons/{weather_icon}.png")

            # --- date & temps ---
            dt = datetime.fromtimestamp(day["dt"], tz=timezone.utc).astimezone(tz)
            day_label = dt.strftime("%a")

            forecast.append(
                {
                    "day": day_label,
                    "high": int(day.get("main")["temp_max"]),
                    "low": int(day.get("main")["temp_min"]),
                    "icon": weather_icon_path,
                }
            )

        return forecast

    def parse_hourly(self, hourly_forecast, tz, time_format):
        hourly = []
        for hour in hourly_forecast[:9]:
            dt = datetime.fromtimestamp(hour.get('dt'), tz=timezone.utc).astimezone(tz)
            hour_forecast = {
                "time": self.format_time(dt, time_format),
                "temperature": int(hour.get("main").get("temp")),
                "precipitiation": hour.get("pop")
            }
            hourly.append(hour_forecast)
        return hourly

    def parse_data_points(self, weather, air_quality, tz, units, time_format):
        data_points = []
        sunrise_epoch = weather.get('city', {}).get("sunrise")

        if sunrise_epoch:
            sunrise_dt = datetime.fromtimestamp(sunrise_epoch, tz=timezone.utc).astimezone(tz)
            if time_format == "24h":
                sunrise_time = sunrise_dt.strftime('%H:%M')
                sunrise_unit = ""
            else:
                sunrise_time = sunrise_dt.strftime('%I:%M').lstrip("0")
                sunrise_unit = sunrise_dt.strftime('%p')
            data_points.append({
                "label": "Lever",
                "measurement": sunrise_time,
                "unit": sunrise_unit,
                "icon": self.get_plugin_dir('icons/sunrise.png')
            })
        else:
            logging.error(f"Sunrise not found in OpenWeatherMap response, this is expected for polar areas in midnight sun and polar night periods.")

        sunset_epoch = weather.get('city', {}).get("sunset")
        if sunset_epoch:
            sunset_dt = datetime.fromtimestamp(sunset_epoch, tz=timezone.utc).astimezone(tz)
            if time_format == "24h":
                sunset_time = sunset_dt.strftime('%H:%M')
                sunset_unit = ""
            else:
                sunset_time = sunset_dt.strftime('%I:%M').lstrip("0")
                sunset_unit = sunset_dt.strftime('%p')
            data_points.append({
                "label": "Coucher",
                "measurement": sunset_time,
                "unit": sunset_unit,
                "icon": self.get_plugin_dir('icons/sunset.png')
            })
        else:
            logging.error(f"Sunset not found in OpenWeatherMap response, this is expected for polar areas in midnight sun and polar night periods.")

        current = weather.get("list")[0]

        data_points.append({
            "label": "Humidité",
            "measurement": current.get('main', {}).get("humidity"),
            "unit": '%',
            "icon": self.get_plugin_dir('icons/humidity.png')
        })

        aqi = air_quality.get('list', [])[0].get("main", {}).get("aqi")
        data_points.append({
            "label": "Qualité de l'air",
            "measurement": aqi,
            "unit": ["Très Bon", "Bon", "Moyen", "Mauvais", "Très Mauvais"][int(aqi)-1],
            "icon": self.get_plugin_dir('icons/aqi.png')
        })

        return data_points

    def get_weather_data(self, api_key, units, lat, long):
        url = FORECAST_URL.format(lat=lat, long=long, units=units, api_key=api_key)
        response = requests.get(url)
        if not 200 <= response.status_code < 300:
            logging.error(f"Failed to retrieve weather data: {response.content}")
            raise RuntimeError("Failed to retrieve weather data.")

        return response.json()

    def get_air_quality(self, api_key, lat, long):
        url = AIR_QUALITY_URL.format(lat=lat, long=long, api_key=api_key)
        response = requests.get(url)

        if not 200 <= response.status_code < 300:
            logging.error(f"Failed to get air quality data: {response.content}")
            raise RuntimeError("Failed to retrieve air quality data.")

        return response.json()

    def get_location(self, api_key, lat, long):
        url = GEOCODING_URL.format(lat=lat, long=long, api_key=api_key)
        response = requests.get(url)

        if not 200 <= response.status_code < 300:
            logging.error(f"Failed to get location: {response.content}")
            raise RuntimeError("Failed to retrieve location.")

        return response.json()[0]

    def format_time(self, dt, time_format, include_am_pm=True):
        """Format datetime based on 12h or 24h preference"""
        if time_format == "24h":
            return dt.strftime("%H:%M")
        else:  # 12h format
            if include_am_pm:
                return dt.strftime("%-I:%M %p")
            else:
                return dt.strftime("%-I:%M")

    def get_local_hour(self, dt, tz):
        return datetime.fromtimestamp(dt, tz=timezone.utc).astimezone(tz).hour
