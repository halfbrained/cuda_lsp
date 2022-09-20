import re
import typing
from collections import OrderedDict
from os import path as op
import os
from time import strftime, time
from subprocess import check_output

import cudatext as ct
import cudatext_cmd
# from cuda_dev import dev

from cudax_lib import get_translation
_   = get_translation(__file__)  # I18N

CT_SNIPPET = 0
VS_SNIPPET = 1
TABSTOP = 0
PLACEHOLDER = 1
RE_DATE = re.compile(r'\${date:(.*?)}')
RE_ENV = re.compile(r'\${env:(.*?)}')
RE_CMD = re.compile(r'\${cmd:(.*?)}')

_tabstop = r"\\?\$(\d+)"
_placeholder_head = r"\${(\d+):?"
_placeholder_tail = "}"
RE_TABSTOP = re.compile(_tabstop)
RE_PLACEHOLDER = re.compile(_placeholder_head)
RE_TOKEN_PART = re.compile(r"(?<!\\)"+_tabstop+'|'+_placeholder_head+'|'+_placeholder_tail)


# https://code.visualstudio.com/docs/editor/userdefinedsnippets?wt.mc_id=devto-blog-chnoring


def is_tabstop(s):
    m = RE_TABSTOP.match(s)
    if not m:
        return False
    elif m[0] == s:
        return True
    else:
        return False


def is_placeholder_head(s):
    m = RE_PLACEHOLDER.match(s)
    if not m:
        return False
    elif m[0] == s:
        return True
    else:
        return False


def is_placeholder_tail(s):
    return True if s == '}' else False


def get_word_under_cursor(line, x, seps='.,:-!<>()[]{}\'"\t\n\r'):
    """get current word under cursor position"""
    if not 0 <= x <= len(line):
        return '', 0
    for sep in seps:
        if sep in line:
            line = line.replace(sep, ' ')
    s = ' ' + line + ' '
    start = s.rfind(' ', 0, x+1)
    end = s.find(' ', x+1) - 1
    word = line[start:end]
    return word, x - start  # word, position cursor in word


def marker(x=0, y=0, tag=0, len_x=0, len_y=0):
    return {
        'id': ct.MARKERS_ADD,
        'x': x,
        'y': y,
        'tag': tag,
        'len_x': len_x,
        'len_y': len_y
    }


class Placeholder:
    __slots__ = ['x0', 'x1', 'y', 'shift', 'tag']

    def __init__(self, x0, x1, y, shift, tag):
        self.x0 = x0
        self.x1 = x1
        self.y = y
        self.shift = shift
        self.tag = tag


class VariableState:
    def __init__(self, ed: ct.Editor):
        self.fp = ed.get_filename()
        if op.exists(self.fp):
            self.fn = op.basename(self.fp)
            self.fdir = op.dirname(self.fp)
        else:
            self.fn = ''
            self.fdir = ''

        x0, y0, x1, y1 = ed.get_carets()[0]
        self.line_index = y0
        self.text_sel = ed.get_text_sel()
        self.clipboard = ct.app_proc(ct.PROC_GET_CLIP, '')
        self.line = ed.get_text_line(y0)
        self.word, _ = get_word_under_cursor(self.line, x0)

        self.lexer = ed.get_prop(ct.PROP_LEXER_FILE)
        prop = ct.lexer_proc(ct.LEXER_GET_PROP, self.lexer)
        if prop:
            prop_str = prop.get('c_str')
            prop_line = prop.get('c_line')
            self.cmt_start = prop_str[0] if prop_str else ''
            self.cmt_end = prop_str[1] if prop_str else ''
            self.cmt_line = prop_line if prop_line else ''
        else:
            self.cmt_start = ''
            self.cmt_end = ''
            self.cmt_line = ''


class Snippet:
    """Base snippet class."""
    __slots__ = ['name', 'id', 'lex', 'text', 'type']

    def __init__(self, name='', id: typing.List = '', lex='', text=None, t=0):
        self.name = name
        self.id = id if isinstance(id, list) else [id]
        self.lex = lex
        self.text = [text] if isinstance(text, str) else text
        self.type = t

    @property
    def _name(self):
        if self.name:
            return self.name
        else:
            return self.id

    def __repr__(self):
        lex = ', '.join(self.lex) if isinstance(self.lex, list) else self.lex
        _id = ', '.join(self.id)
        return self.name + '\t' + _id + '  [' + lex + ']'

    def __eq__(self, o):
        return self is o

    def __ne__(self, o):
        return self is not o

    def __lt__(self, o):
        return self.name < o.name

    def insert(self, ed: ct.Editor):
        if not self.text:
            return
        sn = self.text.copy()

        carets = ed.get_carets()
        if len(carets) != 1:
            return
        x0, y0, x1, y1 = carets[0]

        tab_spaces = ed.get_prop(ct.PROP_TAB_SPACES)
        tab_size = ed.get_prop(ct.PROP_TAB_SIZE)

        # apply indent to lines from second
        x_col, _ = ed.convert(ct.CONVERT_CHAR_TO_COL, x0, y0)
        indent = ' ' * x_col
        if not tab_spaces:
            indent = indent.replace(' ' * tab_size, '\t')
        for i in range(1, len(sn)):
            sn[i] = indent + sn[i]

        # replace tab-chars
        if tab_spaces:
            indent = ' ' * tab_size
            sn = [item.replace('\t', indent) for item in sn]

        # parse variables
        vars_state = VariableState(ed)
        if self.type == VS_SNIPPET:
            sn = self.parse_vars_vs(vars_state, sn)
        else:
            sn = self.parse_vars_ct(vars_state, sn)

        # delete selection
        text_sel = ed.get_text_sel()
        if text_sel:
            # sort coords (x0/y0 is left)
            if (y1 > y0) or ((y1 == y0) and (x1 >= x0)):
                pass
            else:
                x0, y0, x1, y1 = x1, y1, x0, y0
            ed.delete(x0, y0, x1, y1)
            ed.set_caret(x0, y0)

        # parse Tabstops and Placeholders
        _mrks = ed.markers(ct.MARKERS_GET) or {}
        basetag = max([i[-1] for i in _mrks]) if _mrks else 0
        s_text, zero_markers, markers = self.parse_tabstops(sn, x0, y0, basetag=basetag)
        if not s_text:
            print(_('Wrong snippet: {}').format(self.name))
            return

        # insert text
        ed.insert(x0, y0, '\n'.join(s_text))

        # delete old markers
        mark_placed = False
        ed.markers(ct.MARKERS_DELETE_ALL)

        # sync old markers from editor with new text position
        old_zero_markers, old_markers = [], []
        basetag = max([i['tag'] for i in markers]) if markers else 0
        for mk in _mrks:
            x, y = mk[0], mk[1]
            tag = mk[4]

            if tag != 0:
                tag += basetag
            if y > y0:
                y += len(sn) - 1
            elif y == y0 and x > x0:
                x += len(sn[0]) - 1

            m = marker(x, y, tag, mk[2], mk[3])
            if m['tag'] == 0:
                old_zero_markers.append(m)
            else:
                old_markers.append(m)

        old_zero_markers.sort(key=lambda k: k['tag'], reverse=True)

        # insert old markers
        for m in old_zero_markers:
            ed.markers(**m)
        for m in old_markers:
            ed.markers(**m)

        # insert new markers
        for m in zero_markers:
            ed.markers(**m)
            mark_placed = True
        for m in markers:
            ed.markers(**m)
            mark_placed = True

        # this only for new marks
        if mark_placed:
            ed.set_prop(ct.PROP_TAB_COLLECT_MARKERS, '1')
            ed.cmd(cudatext_cmd.cmd_Markers_GotoLastAndDelete)
        else:
            # place caret after text
            if s_text:
                lines_cnt = 0
                for s in s_text:
                    lines_cnt += len(s.splitlines())
                lines_last = s_text[-1].splitlines()
                new_x = len(lines_last[-1])
                if lines_cnt == 1:
                    new_x += x0
                new_y = y0 + lines_cnt - 1
                ed.set_caret(new_x, new_y)

    @staticmethod
    def parse_vars_vs(v, sn):
        variables = {
            # The following variables can be used:
            "TM_SELECTED_TEXT": v.text_sel,  # The currently selected text or the empty string
            "TM_CURRENT_LINE": v.line,  # The contents of the current line
            "TM_CURRENT_WORD": v.word,  # The contents of the word under cursor or the empty string
            "TM_LINE_INDEX": str(v.line_index),  # The zero-index based line number
            "TM_LINE_NUMBER": str(v.line_index + 1),  # The one-index based line number
            "TM_FILEPATH": v.fp,  # The full file path of the current document
            "TM_DIRECTORY": v.fdir,  # The directory of the current document
            "TM_FILENAME": v.fn,  # The filename of the current document
            "TM_FILENAME_BASE": op.splitext(v.fn)[0],  # The filename of the current document without its extensions
            "CLIPBOARD": v.clipboard,  # The contents of your clipboard
            "WORKSPACE_NAME": "",  # The name of the opened workspace or folder

            # For inserting the current date and time:
            "CURRENT_YEAR": strftime('%Y'),  # The current year
            "CURRENT_YEAR_SHORT": strftime('%y'),  # The current year's last two digits
            "CURRENT_MONTH": strftime('%m'),  # The month as two digits (example '02')
            "CURRENT_MONTH_NAME": strftime('%B'),  # The full name of the month (example 'July')
            "CURRENT_MONTH_NAME_SHORT": strftime('%B')[:4],  # The short name of the month (example 'Jul')
            "CURRENT_DATE": strftime('%d'),  # The day of the month
            "CURRENT_DAY_NAME": strftime('%A'),  # The name of day (example 'Monday')
            "CURRENT_DAY_NAME_SHORT": strftime('%a'),  # The short name of the day (example 'Mon')
            "CURRENT_HOUR": strftime('%H'),  # The current hour in 24-hour clock format
            "CURRENT_MINUTE": strftime('%M'),  # The current minute
            "CURRENT_SECOND": strftime('%S'),  # The current second
            "CURRENT_SECONDS_UNIX": str(int(time())),  # The number of seconds since the Unix epoch

            # For inserting line or block comments, honoring the current language:
            "BLOCK_COMMENT_START": v.cmt_start,  # Example output: in PHP /* or in HTML <!--
            "BLOCK_COMMENT_END": v.cmt_end,  # Example output: in PHP */ or in HTML -->
            "LINE_COMMENT": v.cmt_line,  # Example output: in PHP //
        }
        variables = OrderedDict(sorted(variables.items(), reverse=True))

        for i, ln in enumerate(sn):
            # replace VS variables
            for var, v in variables.items():
                ln = ln.replace('$'+var, v)
                ln = ln.replace('${'+var+'}', v)

            sn[i] = ln
        return sn

    @staticmethod
    def parse_vars_ct(v, sn):

        def date_var(ln):
            """${date:format}"""
            start = 0
            _ln = ""
            for p in RE_DATE.finditer(ln):
                _ln += ln[start:p.start(0)] + strftime(p.group(1))
                start = p.end(0)
            _ln += ln[start:]
            return _ln

        def env_var(ln):
            """${env:name}"""
            start = 0
            _ln = ""
            for p in RE_ENV.finditer(ln):
                _ln += ln[start:p.start(0)] + os.environ.get(p.group(1), '')
                start = p.end(0)
            _ln += ln[start:]
            return _ln

        def cmd_var(ln):
            """${cmd:nnnn}"""
            start = 0
            _ln = ""
            for p in RE_CMD.finditer(ln):
                text = p.group(1)
                cwd = os.path.dirname(ct.ed.get_filename()) or os.getcwd()
                text = check_output(text, cwd=cwd, shell=True).decode("utf-8")
                _ln += ln[start:p.start(0)] + text
                start = p.end(0)
            _ln += ln[start:]
            return _ln

        ct_variables = {
            # cudatext macro
            '${sel}': v.text_sel,  # The currently selected text or the empty string
            '${cp}': v.clipboard,
            '${fname}': v.fn,
            '${fpath}': v.fp,
            '${fdir}': v.fdir,
            '${fext}': op.splitext(v.fn)[1],
            '${cmt_start}': v.cmt_start,
            '${cmt_end}': v.cmt_end,
            '${cmt_line}': v.cmt_line,
            '${psep}': os.sep,
        }
        ct_variables = OrderedDict(sorted(ct_variables.items(), reverse=True))

        # replace ct variables
        for i, ln in enumerate(sn):
            ln = date_var(ln)
            ln = env_var(ln)
            ln = cmd_var(ln)
            for var, v in ct_variables.items():
                ln = ln.replace(var, v)
            sn[i] = ln
        return sn

    @staticmethod
    def parse_tabstops(sn, x0, y0, basetag):

        def get_new_ln(new_ln, shift, t):
            return new_ln[:shift+t.start(0)] + new_ln[shift+t.end(0):]

        zero_markers = []
        markers = []

        buf = []
        for y, ln in enumerate(sn):
            new_ln: str = ln
            shift = 0
            for t in RE_TOKEN_PART.finditer(ln):

                if is_tabstop(t[0]):
                    # check for escaped tabstop: "\$n"
                    if t[0][0] == '\\':
                        _start = t.start(0)
                        new_ln = new_ln[:_start] + new_ln[_start+1:]
                        shift -= 1

                    else:   # work normally
                        _tag = int(t[1])
                        m = marker(
                            x=t.start(0) + shift + (x0 if y == 0 else 0),
                            y=y+y0,
                            tag=_tag + basetag
                        )
                        if _tag == 0:
                            zero_markers.append(m)
                        else:
                            markers.append(m)
                        new_ln = get_new_ln(new_ln, shift, t)
                        shift -= len(t[0])

                elif is_placeholder_head(t[0]):
                    p = Placeholder(
                        x0=t.start(0),
                        x1=t.end(0),
                        y=y,
                        shift=shift,
                        tag=int(t[2])
                    )
                    new_ln = get_new_ln(new_ln, shift, t)
                    buf.append(p)
                    shift -= t.end(0) - t.start(0)

                elif is_placeholder_tail(t[0]):
                    if not buf:
                        continue
                    p = buf.pop()
                    x = t.start(0)
                    ln_x = (x + shift) - (p.x0 + p.shift) if y - p.y == 0 else x + shift
                    m = marker(
                        x=p.x0+p.shift+(x0 if y == 0 else 0),
                        y=p.y+y0,
                        tag=p.tag + basetag,
                        len_x=ln_x,
                        len_y=y-p.y
                    )
                    new_ln = get_new_ln(new_ln, shift, t)
                    # dev(m, p.shift, shift, buf)
                    if p.tag == 0:
                        zero_markers.append(m)
                    else:
                        markers.append(m)
                    shift -= 1

            # cln text line
            sn[y] = new_ln

        # convert zero markers to maximum markers if already has markers in editor
        if basetag != 0 and markers:
            basetag = max([mrk['tag'] for mrk in markers])
            for m in zero_markers:
                m['tag'] = basetag
                markers.append(m)
            zero_markers = []

        markers.sort(key=lambda k: k['tag'], reverse=True)
        return sn, zero_markers, markers


# if __name__ == '__main__':
    # ts = VariableState(ct.ed)
    # print(vars(ts))
    # _sn = [
    #     "${date:%Y}",
    #     "${env:PATH}"
    # ]
    # print(Snippet().parse_vars_ct(ts, _sn))

    # _t = [
    #     "--- Class for ${1:description}",
    #     "",
    #     "-- @var class var for this lib",
    #     "local ${TM_FILENAME_BASE/[^a-z]*([a-z]+)/${1:/capitalize}/g} = {}",
    #     "",
    #     "--- Create a new instance",
    #     "-- @param vararg",
    #     "-- @return self",
    #     "function ${TM_FILENAME_BASE/[^a-z]*([a-z]+)/${1:/capitalize}/g}.create( ... )",
    #     "\tlocal self = setmetatable( {}, ${TM_FILENAME_BASE/[^a-z]*([a-z]+)/${1:/capitalize}/g} )",
    #     "\tself:_init( ... )",
    #     "\treturn self",
    #     "end",
    #     "",
    #     "--- Initialize a new instance",
    #     "-- @private",
    #     "-- @param vararg",
    #     "-- @return self",
    #     "function ${TM_FILENAME_BASE/[^a-z]*([a-z]+)/${1:/capitalize}/g}:_init( ... ) -- luacheck: no unused args",
    #     "\tlocal t = { ... }",
    #     "\t--@todo must probably be completed",
    #     "\treturn self",
    #     "end",
    #     "",
    #     "$0",
    #     "",
    #     "-- Return the final class",
    #     "return ${TM_FILENAME_BASE/[^a-z]*([a-z]+)/${1:/capitalize}/g}"
    # ]
    # _sn = Snippet(name='1', id='1', lex='lua', text=_t, t=VS_SNIPPET)
    # print(_sn.insert(ct.ed))
