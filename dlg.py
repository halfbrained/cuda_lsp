
#from cudatext import *
import cudatext as ct


from .sansio_lsp_client.structs import MarkupKind
from .sansio_lsp_client.events import Hover

ed = ct.ed
dlg_proc = ct.dlg_proc

FORM_W = 550
FORM_H = 350
FORM_GAP = 4

COLOR_FORM_BACK = 0x505050

def is_mouse_in_form(h_dlg):
    prop = dlg_proc(h_dlg, ct.DLG_PROP_GET)
    if not prop['vis']: return False
    w = prop['w']
    h = prop['h']

    x, y = ct.app_proc(ct.PROC_GET_MOUSE_POS, '')
    x, y = dlg_proc(h_dlg, ct.DLG_COORD_SCREEN_TO_LOCAL, index=x, index2=y)

    return (0<=x<w and 0<=y<h)

def cursor_dist(pos):
    cursor_pos = ct.app_proc(ct.PROC_GET_MOUSE_POS, '')
    dist_sqr = (pos[0]-cursor_pos[0])**2 + (pos[1]-cursor_pos[1])**2
    return dist_sqr**0.5

class Hint:
    """ Short-lived dialog with 'Editor', hidden when mouse leaves it
    """
    h = None
    theme_name = None

    @classmethod
    def init_form(cls):
        h = dlg_proc(0, ct.DLG_CREATE)

        colors = ct.app_proc(ct.PROC_THEME_UI_DICT_GET, '')
        color_form_bg = colors['TabBorderActive']['color']

        dlg_proc(h, ct.DLG_PROP_SET, prop={
                'w': FORM_W + 2*FORM_GAP,
                'border': False,
                'color': color_form_bg,
                # doesn't work well with embedded Editor -- using timer hide_check_timer()
                #'on_mouse_exit': cls.dlgcolor_mouse_exit,
                })

        n = dlg_proc(h, ct.DLG_CTL_ADD, 'editor')
        dlg_proc(h, ct.DLG_CTL_PROP_SET, index=n, prop={
                'align': ct.ALIGN_CLIENT,
                'sp_a': FORM_GAP,
                'h': FORM_H,
                })
        h_ed = dlg_proc(h, ct.DLG_CTL_HANDLE, index=n)
        # Editor.set_text_all() doesn't clutter edit history, so no unnecessary stuff is stored in RAM
        ed = ct.Editor(h_ed)

        ed.set_prop(ct.PROP_GUTTER_ALL, False)

        cls.theme_name = ct.app_proc(ct.PROC_THEME_UI_GET, '')

        return h, ed

    @classmethod
    def show(cls, text, markupkind=MarkupKind.PLAINTEXT, caret=None):
        if not text:
            return

        if cls.h is None  or  cls.is_theme_changed():
            if cls.h is not None: # theme changed
                ct.dlg_proc(cls.h, ct.DLG_FREE)

            cls.h, cls.ed = cls.init_form()

        cls.cursor_pos = ct.app_proc(ct.PROC_GET_MOUSE_POS, '')
        scale_UI_percent, _scale_font_percent = ct.app_proc(ct.PROC_CONFIG_SCALE_GET, '')
        cls.cursor_margin = 15 * scale_UI_percent*0.01 # 15px scaled


        cls.ed.set_prop(ct.PROP_RO, False)

        cls.ed.set_text_all(text)
        cls.ed.set_prop(ct.PROP_LINE_TOP, 0)

        if markupkind == MarkupKind.MARKDOWN:
            cls.ed.set_prop(ct.PROP_LEXER_FILE, 'Markdown')
        elif markupkind == MarkupKind.PLAINTEXT:
            cls.ed.set_prop(ct.PROP_LEXER_FILE, None)

        cls.ed.set_prop(ct.PROP_RO, True)

        if caret is not None:

            l,t,r,b = ed.get_prop(ct.PROP_RECT_TEXT) # l,t,r,b
            cell_w, cell_h = ed.get_prop(ct.PROP_CELL_SIZE)
            ed_size_x = r - l # text area sizes - to not obscure other ed-controls
            pos = ed.convert(ct.CONVERT_CARET_TO_PIXELS, x=caret[0], y=caret[1])

            top_hint = pos[1]-t > b-pos[1] # space up is larger than down
            y0,y1 = (t, pos[1])  if top_hint else  (pos[1], b)
            h = min(FORM_H,  y1-y0 - FORM_GAP*2 - cell_h)
            w = min(FORM_W, ed_size_x)

            x = pos[0] - int(w*0.5) # center over caret
            if x < l: # dont fit on left
                x = l + FORM_GAP
            elif x+w > r: # dont fit on right
                x = r - w - FORM_GAP

            y = (pos[1] - (h + cell_h + FORM_GAP))  if top_hint else  (pos[1] + cell_h + FORM_GAP)

            dlg_proc(cls.h, ct.DLG_PROP_SET, prop={
                    'p': ed.get_prop(ct.PROP_HANDLE_SELF ), #set parent to Editor handle
                    'x': x,
                    'y': y,
                    'w': w,
                    'h': h,
                    })
        #end if
        # first - large delay, after - smaller
        ct.timer_proc(ct.TIMER_START_ONE, Hint.hide_check_timer, 750, tag='initial')
        dlg_proc(cls.h, ct.DLG_SHOW_NONMODAL)

    @classmethod
    def hide_check_timer(cls, tag='', info=''):
        # hide if not over dialog  and  cursor moved at least ~15px
        if not is_mouse_in_form(cls.h)  and  cursor_dist(cls.cursor_pos) > cls.cursor_margin:
            ct.timer_proc(ct.TIMER_STOP, Hint.hide_check_timer, 250, tag='')

            # clear editor data and hide dialog
            cls.ed.set_prop(ct.PROP_RO, False)
            cls.ed.set_text_all('')
            dlg_proc(cls.h, ct.DLG_HIDE)

        if tag == 'initial': # give some time to move mouse to dialog
            ct.timer_proc(ct.TIMER_START, Hint.hide_check_timer, 250, tag='')

    @classmethod
    def is_theme_changed(cls):
        old_name = cls.theme_name
        cls.theme_name = ct.app_proc(ct.PROC_THEME_UI_GET, '')
        return old_name != cls.theme_name

    @classmethod
    def is_visible(cls):
        if cls.h is None:
            return False
        return dlg_proc(cls.h, ct.DLG_PROP_GET)['vis']

