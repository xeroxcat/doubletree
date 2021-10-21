#!/usr/bin/env python3

from collections import namedtuple

import urwid as ur

class ExpandingList(ur.WidgetWrap):
    def __init__(self):
        walker = ur.SimpleFocusListWalker(
                [ur.Columns([ur.Text("-"), ur.Text("-")])])
        self.listbox = ur.ListBox(walker)
        self.summary = ur.Columns([("pack", ur.SelectableIcon("\u2261 ")),
                                   ur.Text(self.selected())])
        super().__init__(self.summary)


    def keypress(self, size, key):
        if key == "tab":
            if self._w == self.summary:
                height = min(3, len(self.listbox.body))
                self._w = ur.BoxAdapter(self.listbox, height)
            else:
                self.load_summary()
        elif (res := super().keypress(size, key)):
            return res


    def load_list(self, str_list):
        self.listbox.body = ur.SimpleFocusListWalker(
            [ur.Columns([("pack", ur.SelectableIcon('- ')),
                         ur.Text(string)]) for string in str_list])
        self.load_summary()


    def selected(self):
        return self.listbox.focus[1].get_text()[0]


    def load_summary(self):
        self.summary[1].set_text(self.selected())
        self._w = self.summary


class TableList(ur.ListBox):
    def __init__(self, col_header):
        header_widgets = []
        for header in col_header:
            header_widgets.append(TableItem(header, wrap="clip"))
        self.header = TableRow("header", header_widgets)
        super().__init__(ur.SimpleFocusListWalker([self.header]))


    def add_row(self, key, contents):
        self.body.append(TableRow(key, contents))


    def align(self):
        pass


    def selected(self):
        if self.focus:
            if self.focus.key == "header":
                return (None, self.focus.focus.key)
            else:
                return (self.focus.key, self.focus.focus.key)
        return (None, None)


    def selected_col(self):
        """return header key of currently selected column"""
        if self.focus:
            focus_idx = self.focus.find(self.focus.focus.key)
            return self.header[focus_idx].key


    def __getitem__(self, key):
        """return the row with the given key"""
        if key is None:
            return self.header
        for item in self.body:
            if item.key == key:
                return item


    def index(self, key):
        if key is None:
            return 0
        for idx, item in enumerate(self.body):
            if item.key == key:
                return idx


    def sort_by(self, column):
        rows = []
        col_idx = self.body[0].find(column)
        for row in self.body[1:]:
            col_val = row[col_idx].sort
            rows.append((col_val, row))
        self.body = ur.SimpleFocusListWalker(
                [self.body[0]]
                + [row[1] for row in sorted(rows, key=lambda r: r[0])])


class TableItem(ur.WidgetWrap):
    def __init__(self, key, text=None, sort=None, selectable=True,
                 align="left", wrap="ellipsis"):
        self.key = key
        if text is None:
            text = str(key)
        if selectable:
            widget = SelectableText(text, align=align, wrap=wrap)
        else:
            widget = ur.Text(text, align=align, wrap=wrap)
        if sort is None:
            sort = text
        self.sort = sort
        super().__init__(widget)


class SelectableText(ur.SelectableIcon):
    def __init__(self, text, *args, cursor_position=0, **kwargs):
        super(ur.SelectableIcon, self).__init__(text, *args, **kwargs)
        self._cursor_position = cursor_position


class TableRow(ur.Columns):
    def __init__(self, key, widget_list):
        self.key = key
        super().__init__(widget_list)


    def find(self, key):
        for idx, (item, _) in enumerate(self.contents):
            if item.key == key:
                return idx


    def __getitem__(self, idx):
        return self.contents[idx][0]
