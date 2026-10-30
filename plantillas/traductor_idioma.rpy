# Archivo añadido por el Traductor Ren'Py. No modifica el código del juego:
# solo añade un botón para cambiar entre el idioma original y la traducción.
# Para quitarlo, borra este archivo y su .rpyc.
#
#   - Ren'Py 7.5 / 8 o superior: botón en el menú principal y en los menús
#     del juego (config.always_shown_screens).
#   - Ren'Py 6.99.11 a 7.4: botón durante la partida (config.overlay_screens).
#   - Cualquier versión: atajo de teclado Mayús+L.
#   - Las fuentes del juego sin tildes ni ñ se cambian por DejaVuSans
#     mientras el juego está traducido.

init 999 python:

    # Las funciones de Python se toman de builtins: un juego puede tener un
    # personaje que se llame igual (define max = Character("Max")) y pisarlas.
    try:
        import builtins as _traductor_py
    except ImportError:
        import __builtin__ as _traductor_py

    traductor_idioma = "@@IDIOMA@@"
    traductor_nombre = u"@@NOMBRE@@"

    def traductor_activo():
        return _preferences.language == traductor_idioma

    def traductor_etiqueta():
        if traductor_activo():
            return traductor_nombre
        return u"Original"

    def traductor_cambiar():
        if traductor_activo():
            renpy.change_language(None)
        else:
            renpy.change_language(traductor_idioma)
        renpy.restart_interaction()

    traductor_tamano = _traductor_py.max(14, _traductor_py.int(config.screen_height / 36))

    if _traductor_py.hasattr(config, "always_shown_screens"):
        traductor_siempre = False
        config.always_shown_screens.append("traductor_idioma")
    else:
        traductor_siempre = True
        config.overlay_screens.append("traductor_idioma")

    config.keymap["traductor_idioma"] = [ "shift_K_l" ]
    config.underlay.append(renpy.Keymap(traductor_idioma=traductor_cambiar))

    # Fuentes del juego que no tienen tildes ni ñ. Mientras el juego está en
    # la traducción se dibujan con DejaVuSans, que viene con Ren'Py; en el
    # idioma original no cambia nada. Se compara solo el nombre del archivo
    # porque cada juego escribe la ruta a su manera.
    traductor_fuentes = @@FUENTES@@
    traductor_texto = (_traductor_py.type(u""), _traductor_py.type(""))

    class TraductorMapaFuentes(_traductor_py.dict):

        def get(self, key, default=None):
            rv = _traductor_py.dict.get(self, key, None)
            if rv is not None:
                return rv
            try:
                fn, bold, italics = key
            except Exception:
                return default
            if (traductor_activo() and _traductor_py.isinstance(fn, traductor_texto)
                    and fn.replace("\\", "/").split("/")[-1].lower() in traductor_fuentes):
                if bold:
                    return ("DejaVuSans-Bold.ttf", False, italics)
                return ("DejaVuSans.ttf", False, italics)
            return default

    if traductor_fuentes:
        config.font_replacement_map = TraductorMapaFuentes(config.font_replacement_map)

screen traductor_idioma:
    zorder 1000

    if traductor_siempre or main_menu or _menu:
        button:
            xalign 1.0
            yalign 0.0
            xoffset -8
            yoffset 8
            xpadding 14
            ypadding 6
            background "#000000b0"
            hover_background "#303030e0"
            action traductor_cambiar

            text traductor_etiqueta():
                size traductor_tamano
                color "#ffffff"
                hover_color "#ffd54a"
                outlines [ (1, "#000000", 0, 0) ]
