
from cudatext import *
#import cudatext as ct

# imported on ~access
#from .sansio_lsp_client.structs import MarkupKind

FORM_W = 550
FORM_H = 350
BUTTON_H = 20
ED_MAX_LINES = 10
FORM_GAP = 4

CURSOR_MOVE_TOLERANCE = 30

def is_mouse_in_form(h_dlg):
    prop = dlg_proc(h_dlg, DLG_PROP_GET)
    if not prop['vis']: return False
    w = prop['w']
    h = prop['h']

    x, y = app_proc(PROC_GET_MOUSE_POS, '')
    x, y = dlg_proc(h_dlg, DLG_COORD_SCREEN_TO_LOCAL, index=x, index2=y)

    return (0<=x<w and 0<=y<h)

def cursor_dist(pos):
    cursor_pos = app_proc(PROC_GET_MOUSE_POS, '')
    dist_sqr = (pos[0]-cursor_pos[0])**2 + (pos[1]-cursor_pos[1])**2
    return dist_sqr**0.5

# cant invoke method on 'Hint' class
def hint_callback(id_dlg, id_ctl, data='', info=''):
    Hint.on_widget_click(id_ctl, info)


class Hint:
    """ Short-lived dialog with 'Editor', hidden when mouse leaves it
    """
    h = None
    theme_name = None
    current_caret = None
    def func(*args, **vargs):
        print(f'**** dlg innner')

    @classmethod
    def init_form(cls):
        global MarkupKind
        global FORM_H

        from .sansio_lsp_client.structs import MarkupKind

        _cell_w, cell_h = ed.get_prop(PROP_CELL_SIZE)
        FORM_H = FORM_GAP*2 + ED_MAX_LINES*cell_h + BUTTON_H

        h = dlg_proc(0, DLG_CREATE)

        colors = app_proc(PROC_THEME_UI_DICT_GET, '')
        color_form_bg = colors['TabBorderActive']['color']
        cls.color_btn_font = colors['ButtonFont']['color']
        cls.color_btn_back = colors['ButtonBgPassive']['color']
        cls.color_btn_font_disabled = colors['ButtonFontDisabled']['color']
        cls.color_btn_back_disabled = colors['ButtonBgDisabled']['color']

        dlg_proc(h, DLG_PROP_SET, prop={
                'w': FORM_W + 2*FORM_GAP,
                'border': False,
                'color': color_form_bg,
                # doesn't work well with embedded Editor -- using timer hide_check_timer()
                #'on_mouse_exit': cls.dlgcolor_mouse_exit,
                })

        cls._n_sb = dlg_proc(h, DLG_CTL_ADD, 'statusbar')
        dlg_proc(h, DLG_CTL_PROP_SET, index=cls._n_sb, prop={
                'align': ALIGN_BOTTOM,
                'sp_l': 1,
                'sp_r': 1,
                'sp_b': 1,
                'h': BUTTON_H,
                #'w': 128,
                #'w_max': 128,
                })
        cls._h_sb = dlg_proc(h, DLG_CTL_HANDLE, index=cls._n_sb)

        n = dlg_proc(h, DLG_CTL_ADD, 'editor')
        dlg_proc(h, DLG_CTL_PROP_SET, index=n, prop={
                'align': ALIGN_CLIENT,
                'sp_a': FORM_GAP,
                'h': FORM_H,
                })
        h_ed = dlg_proc(h, DLG_CTL_HANDLE, index=n)
        # Editor.set_text_all() doesn't clutter edit history, so no unnecessary stuff is stored in RAM
        edt = Editor(h_ed)

        edt.set_prop(PROP_GUTTER_ALL, False)
        edt.set_prop(PROP_MINIMAP, False)
        edt.set_prop(PROP_MICROMAP, False)
        edt.set_prop(PROP_LAST_LINE_ON_TOP, False)

        cls.theme_name = app_proc(PROC_THEME_UI_GET, '')

        dlg_proc(h, DLG_SCALE)
        return h, edt

    # language - from deprecated 'MarkedString'
    @classmethod
    def show(cls, text, caret, cursor_loc_start, markupkind=None, language=None, caret_cmds=None):
        if not text:
            return

        if cls.h is None  or  cls.is_theme_changed():
            if cls.h is not None: # theme changed
                dlg_proc(cls.h, DLG_FREE)

            cls.h, cls.ed = cls.init_form()

        cls.current_caret = caret # for 'Go to Definition'
        cls.cursor_pos = app_proc(PROC_GET_MOUSE_POS, '')
        _scale_UI_percent, _scale_font_percent = app_proc(PROC_CONFIG_SCALE_GET, '')
        cls.cursor_margin = CURSOR_MOVE_TOLERANCE * _scale_UI_percent*0.01 # ~30px scaled

        ### dont show dialog if cursor moved from request-position
        _glob_cursor_start = ed.convert(CONVERT_LOCAL_TO_SCREEN, *cursor_loc_start)
        if cursor_dist(_glob_cursor_start) > cls.cursor_margin:
            return

        ### dialog Editor setup
        cls.ed.set_prop(PROP_RO, False)
        try:
            cls.ed.set_text_all(text)
            cls.ed.set_prop(PROP_LINE_TOP, 0)
            cls.ed.set_prop(PROP_SCROLL_HORZ, 0)

            if markupkind == MarkupKind.MARKDOWN:
                cls.ed.set_prop(PROP_LEXER_FILE, 'Markdown')
            else:
                cls.ed.set_prop(PROP_LEXER_FILE, None)
        finally:
            cls.ed.set_prop(PROP_RO, True)

        ### calculate dialog position and dimensions: x,y, h,w
        l,t,r,b = ed.get_prop(PROP_RECT_TEXT)
        cell_w, cell_h = ed.get_prop(PROP_CELL_SIZE)
        ed_size_x = r - l # text area sizes - to not obscure other ed-controls

        caret_loc_px = ed.convert(CONVERT_CARET_TO_PIXELS, x=caret[0], y=caret[1])
        top_hint = caret_loc_px[1]-t > b-caret_loc_px[1] # space up is larger than down
        y0,y1 = (t, caret_loc_px[1])  if top_hint else  (caret_loc_px[1], b)
        h = min(FORM_H,  y1-y0 - FORM_GAP*2 - cell_h)
        w = min(FORM_W, ed_size_x)

        x = caret_loc_px[0] - int(w*0.5) # center over caret
        if x < l: # dont fit on left
            x = l + FORM_GAP
        elif x+w > r: # dont fit on right
            x = r - w - FORM_GAP

        if top_hint:
            y = (caret_loc_px[1] - (h + FORM_GAP))
        else:
            y = (caret_loc_px[1] + cell_h + FORM_GAP)


        dlg_proc(cls.h, DLG_PROP_SET, prop={
                'p': ed.get_prop(PROP_HANDLE_SELF ), #set parent to Editor handle
                'x': x,
                'y': y,
                'w': w,
                'h': h,
                })

        cls.caret_cmds = caret_cmds
        if caret_cmds:
            cls.fill_cmds(caret_cmds, w)

        # first - large delay, after - smaller
        timer_proc(TIMER_START_ONE, Hint.hide_check_timer, 750, tag='initial')
        dlg_proc(cls.h, DLG_SHOW_NONMODAL)

    @classmethod
    def fill_cmds(cls, cmds, width):
        statusbar_proc(cls._h_sb, STATUSBAR_DELETE_ALL)

        cellwidth = int(width/len(cmds)) + 1
        callback_fstr = 'module=cuda_lsp.dlg;func=hint_callback;info="{}";'
        for caption,cmd in cmds.items():
            cellind = statusbar_proc(cls._h_sb, STATUSBAR_ADD_CELL, index=-1)
            statusbar_proc(cls._h_sb, STATUSBAR_SET_CELL_TEXT, index=cellind, value=caption)
            statusbar_proc(cls._h_sb, STATUSBAR_SET_CELL_SIZE, index=cellind, value=cellwidth)

            if cmd:
                bg,fg = cls.color_btn_back,  cls.color_btn_font

                callback = callback_fstr.format(caption)
                statusbar_proc(cls._h_sb, STATUSBAR_SET_CELL_CALLBACK, index=cellind, value=callback)
            else:
                bg,fg = cls.color_btn_back_disabled,  cls.color_btn_font_disabled

            statusbar_proc(cls._h_sb,  STATUSBAR_SET_CELL_COLOR_BACK, index=cellind, value=bg)
            statusbar_proc(cls._h_sb,  STATUSBAR_SET_CELL_COLOR_FONT, index=cellind, value=fg)


    @classmethod
    def on_widget_click(cls, n, info):
        if n == cls._n_sb:
            f = cls.caret_cmds.get(info)
            if f:
                f(caret=cls.current_caret)


    @classmethod
    def set_max_lines(cls, nlines):
        global ED_MAX_LINES

        ED_MAX_LINES = nlines

    @classmethod
    def hide_check_timer(cls, tag='', info=''):
        # hide if not over dialog  and  cursor moved at least ~15px
        if not is_mouse_in_form(cls.h)  and  cursor_dist(cls.cursor_pos) > cls.cursor_margin:
            timer_proc(TIMER_STOP, Hint.hide_check_timer, 250, tag='')

            cls.hide()
            ed.focus()

        if tag == 'initial': # give some time to move mouse to dialog
            timer_proc(TIMER_START, Hint.hide_check_timer, 250, tag='')

    @classmethod
    def hide(cls):
        # clear editor data and hide dialog
        cls.ed.set_prop(PROP_RO, False)
        cls.ed.set_text_all('')
        cls.current_caret = None
        dlg_proc(cls.h, DLG_HIDE)

    @classmethod
    def is_theme_changed(cls):
        old_name = cls.theme_name
        cls.theme_name = app_proc(PROC_THEME_UI_GET, '')
        return old_name != cls.theme_name

    @classmethod
    def is_visible(cls):
        if cls.h is None:
            return False
        return dlg_proc(cls.h, DLG_PROP_GET)['vis']

    @classmethod
    def is_under_cursor(cls):
        return cls.is_visible()  and  is_mouse_in_form(cls.h)
