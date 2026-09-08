"""Registra i blueprint. Ogni modulo espone `bp`. Un modulo mancante NON deve far cadere l'app."""
import importlib
import logging

MODULES = ["boot", "api_system", "api_sources", "api_catalog", "api_menu", "api_clients", "api_logs", "api_upload", "api_drivers", "api_answers", "answers_public", "api_settings", "api_winprofile", "api_debprofile", "api_presets", "api_cache", "api_backup", "api_update"]


def register_all(app):
    for m in MODULES:
        try:
            mod = importlib.import_module(f"pixio.blueprints.{m}")
            app.register_blueprint(mod.bp)
        except Exception as e:  # noqa
            logging.getLogger("pixio").error("blueprint %s non caricato: %s", m, e)
