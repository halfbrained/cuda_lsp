
#from cudatext import *
import cudatext as ct


from .sansio_lsp_client.structs import MarkupKind
from .sansio_lsp_client.events import Hover

ed = ct.ed
dlg_proc = ct.dlg_proc

FORM_W = 650
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

    return 0<=x<w and 0<=y<h

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

        cls.ed.set_prop(ct.PROP_RO, False)

        cls.ed.set_text_all(text)
        cls.ed.set_prop(ct.PROP_LINE_TOP, 0)

        if markupkind == MarkupKind.MARKDOWN:
            cls.ed.set_prop(ct.PROP_LEXER_FILE, 'Markdown')
        elif markupkind == MarkupKind.PLAINTEXT:
            cls.ed.set_prop(ct.PROP_LEXER_FILE, None)

        cls.ed.set_prop(ct.PROP_RO, True)

        if caret is not None:
            _prop = dlg_proc(cls.h, ct.DLG_PROP_GET)
            form_w = _prop['w']
            form_h = _prop['h']

            _pos_x = caret[0]
            _pos_y = caret[1]
            pos = ed.convert(ct.CONVERT_CARET_TO_PIXELS, x=_pos_x, y=_pos_y)

            #gap_out = FORM_GAP_OUT_COLOR if h_dlg==self.h_dlg_color else FORM_GAP_OUT
            _cell_size = ed.get_prop(ct.PROP_CELL_SIZE)
            _ed_coord = ed.get_prop(ct.PROP_COORDS)
            ed_size_x = _ed_coord[2]-_ed_coord[0]
            ed_size_y = _ed_coord[3]-_ed_coord[1]
            hint_x = pos[0]
            hint_y = pos[1] + _cell_size[1] #+ gap_out

            #no space on bottom?
            if hint_y + form_h > ed_size_y:
                hint_y = pos[1] - form_h #- gap_out

            #no space on right?
            if hint_x + form_w > ed_size_x:
                hint_x = ed_size_x - form_w

            dlg_proc(cls.h, ct.DLG_PROP_SET, prop={
                    'p': ed.get_prop(ct.PROP_HANDLE_SELF ), #set parent to Editor handle
                    'x': hint_x,
                    'y': hint_y,
                    })
        #end if
        # first - large delay, after - smaller
        ct.timer_proc(ct.TIMER_START_ONE, Hint.hide_check_timer, 1500, tag='initial')
        dlg_proc(cls.h, ct.DLG_SHOW_NONMODAL)

    @classmethod
    def hide_check_timer(cls, tag='', info=''):
        if not is_mouse_in_form(cls.h):
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

