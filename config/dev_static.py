from django.contrib.staticfiles.handlers import StaticFilesHandlerMixin


DEV_STATIC_CACHE_CONTROL = "no-store, no-cache, must-revalidate, max-age=0"


def enable_dev_static_no_cache() -> None:
    if getattr(StaticFilesHandlerMixin, "_hitech_dev_no_cache_enabled", False):
        return

    original_serve = StaticFilesHandlerMixin.serve

    def serve(self, request):
        response = original_serve(self, request)
        response["Cache-Control"] = DEV_STATIC_CACHE_CONTROL
        response["Pragma"] = "no-cache"
        response["Expires"] = "0"
        return response

    StaticFilesHandlerMixin.serve = serve
    StaticFilesHandlerMixin._hitech_dev_no_cache_enabled = True
