
#from cudatext import *
import cudatext as ct

# imported on ~access
#from .sansio_lsp_client.structs import MarkupKind

ed = ct.ed
dlg_proc = ct.dlg_proc

FORM_W = 550
FORM_H = 350
BUTTON_H = 20
ED_MAX_LINES = 10
FORM_GAP = 4

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
    current_caret = None

    @classmethod
    def init_form(cls):
        global MarkupKind
        global FORM_H

        from .sansio_lsp_client.structs import MarkupKind

        _cell_w, cell_h = ed.get_prop(ct.PROP_CELL_SIZE)
        FORM_H = FORM_GAP*2 + ED_MAX_LINES*cell_h + BUTTON_H

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

        n = dlg_proc(h, ct.DLG_CTL_ADD, 'button_ex')
        dlg_proc(h, ct.DLG_CTL_PROP_SET, index=n, prop={
                'align': ct.ALIGN_BOTTOM,
                #'sp_a': FORM_GAP,
                'h': BUTTON_H,
                #'w': 128,
                #'w_max': 128,
                'on_change': cls.on_definition_click,
                })
        h_def = dlg_proc(h, ct.DLG_CTL_HANDLE, index=n)
        ct.button_proc(h_def, ct.BTN_SET_TEXT, 'Go to Definition')

        n = dlg_proc(h, ct.DLG_CTL_ADD, 'editor')
        dlg_proc(h, ct.DLG_CTL_PROP_SET, index=n, prop={
                'align': ct.ALIGN_CLIENT,
                'sp_a': FORM_GAP,
                'h': FORM_H,
                })
        h_ed = dlg_proc(h, ct.DLG_CTL_HANDLE, index=n)
        # Editor.set_text_all() doesn't clutter edit history, so no unnecessary stuff is stored in RAM
        edt = ct.Editor(h_ed)

        edt.set_prop(ct.PROP_GUTTER_ALL, False)
        edt.set_prop(ct.PROP_MINIMAP, False)
        edt.set_prop(ct.PROP_MICROMAP, False)

        cls.theme_name = ct.app_proc(ct.PROC_THEME_UI_GET, '')

        dlg_proc(h, ct.DLG_SCALE)
        return h, edt

    @classmethod
    def show(cls, text, markupkind=None, caret=None):
        if not text:
            return

        if cls.h is None  or  cls.is_theme_changed():
            if cls.h is not None: # theme changed
                ct.dlg_proc(cls.h, ct.DLG_FREE)

            cls.h, cls.ed = cls.init_form()

        cls.current_caret = caret # for 'Go to Definition'

        cls.cursor_pos = ct.app_proc(ct.PROC_GET_MOUSE_POS, '')
        scale_UI_percent, _scale_font_percent = ct.app_proc(ct.PROC_CONFIG_SCALE_GET, '')
        cls.cursor_margin = 15 * scale_UI_percent*0.01 # 15px scaled

        cls.ed.set_prop(ct.PROP_RO, False)

        cls.ed.set_text_all(text)
        cls.ed.set_prop(ct.PROP_LINE_TOP, 0)
        cls.ed.set_prop(ct.PROP_SCROLL_HORZ, 0)

        if markupkind == MarkupKind.MARKDOWN:
            cls.ed.set_prop(ct.PROP_LEXER_FILE, 'Markdown')
        else:
            cls.ed.set_prop(ct.PROP_LEXER_FILE, None)

        cls.ed.set_prop(ct.PROP_RO, True)

        if caret is not None:
            pos = ed.convert(ct.CONVERT_CARET_TO_PIXELS, x=caret[0], y=caret[1])

            # dont show dialog if cursor moved from request-position
            _glob_cursor = ed.convert(ct.CONVERT_LOCAL_TO_SCREEN, *pos)
            if pos is None  or  cursor_dist(_glob_cursor) > cls.cursor_margin:
                return

            l,t,r,b = ed.get_prop(ct.PROP_RECT_TEXT) # l,t,r,b
            cell_w, cell_h = ed.get_prop(ct.PROP_CELL_SIZE)
            ed_size_x = r - l # text area sizes - to not obscure other ed-controls

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
    def on_definition_click(cls, id_dlg, id_ctl, data='', info=''):
        if cls.current_caret:
            caret_str = '|'.join(map(str, cls.current_caret))
            ct.app_proc(ct.PROC_EXEC_PLUGIN, 'cuda_lsp,caret_definition,' + caret_str)

    @classmethod
    def set_max_lines(cls, nlines):
        global ED_MAX_LINES

        ED_MAX_LINES = nlines

    @classmethod
    def hide_check_timer(cls, tag='', info=''):
        # hide if not over dialog  and  cursor moved at least ~15px
        if not is_mouse_in_form(cls.h)  and  cursor_dist(cls.cursor_pos) > cls.cursor_margin:
            ct.timer_proc(ct.TIMER_STOP, Hint.hide_check_timer, 250, tag='')

            cls.hide()
            ed.focus()

        if tag == 'initial': # give some time to move mouse to dialog
            ct.timer_proc(ct.TIMER_START, Hint.hide_check_timer, 250, tag='')

    @classmethod
    def hide(cls):
        # clear editor data and hide dialog
        cls.ed.set_prop(ct.PROP_RO, False)
        cls.ed.set_text_all('')
        cls.current_caret = None
        dlg_proc(cls.h, ct.DLG_HIDE)

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

    @classmethod
    def is_under_cursor(cls):
        return cls.is_visible()  and  is_mouse_in_form(cls.h)
