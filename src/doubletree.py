#!/usr/bin/env python3

import os
import logging
import functools
from datetime import datetime
import traceback

from rdflib.namespace import RDF, RDFS, OWL, XSD
from pyswip.prolog import Prolog, PrologError
import urwid as ur

import mpd_util
from palette import palette
from log_util import LogFormatter
from mpd_player import MpdPlayer

from rdf_util.namespaces import XCAT
from rdf_util.pl import mixed_query, fill_query, all_classes, RPQ, VarList

tree_views = {
    'instance_list': {
        'query': [
            ['URI',
             f"rdfs_individual_of(URI, InstanceClass)",
             '[{Class}] {Label} <{URI}>',
             ('InstanceClass', None),
             'xcat_print(URI, Class, Label)',
             dict(null=True)]
        ], 'root': RDFS.Resource},
    'artist_releases': {
        'query': [
            ['Artist',
             f'rdfs_individual_of(Artist, Class), xcat_print(Artist, _, Name)',
             '{Name} <{Artist}>',
             ('Class', None),
             f'xcat_has_releases(Artist, _)',
            ],
            ['Album',
             f"rdf(Artist, '{XCAT.made}', Album), xcat_print(Album, _, Name), "
             f"rdf(Album, '{RDF.type}', '{XCAT.Release}')",
             '{Name} <{Album}>',
             ('Artist', None)
            ],
            ['[Track]',
             "xcat_tracklist(Release, Track)",
             '{TLabel} <{Track}>',
             ('Release', None),
             f"xcat_print(Track, _, TLabel)",
             dict(q_by=False)
            ],
        ], 'root': XCAT.Artist,
        },
}

instance_ops = {
    str(XCAT.Recording): {
        'enter': (('xcat_filepath', ('_k:key', '_v:Path')),
                  mpd_util.add_to_list),
        },
    str(XCAT.Release): {
        'enter': (('xcat_tracklist_filepaths', ('_k:key', '_v:Paths')),
                  mpd_util.add_to_list),
        }
    }

class RPQ_NodeText(ur.TreeWidget):
    unexpanded_icon = ur.wimp.SelectableIcon('\u25B6', 0)
    expanded_icon = ur.wimp.SelectableIcon('\u25B7', 0)
    leaf_icon = ur.wimp.SelectableIcon('-', 0)

    def __init__(self, node):
        super().__init__(node)
        self.expanded = False
        self.update_expanded_icon()


    def selectable(self):
        return True


    def get_display_text(self):
        return str(self.get_node().get_value())


    def get_indented_widget(self):
        inner = self.get_inner_widget()
        widget = ur.Columns([('fixed', 1, self.get_icon()), inner],
                            dividechars=1)
        indent_cols = self.get_indent_cols()
        return ur.Padding(widget, width=('relative', 100), left=indent_cols)


    def get_icon(self):
        if self.is_leaf:
            return self.leaf_icon
        elif self.expanded:
            return self.expanded_icon
        else:
            return self.unexpanded_icon


    def update_expanded_icon(self):
        """Update display widget text for parent widgets"""
        # icon is first element in columns indented widget
        self._w.base_widget.widget_list[0] = self.get_icon()


    def keypress(self, size, key):
        if key == "tab":
            if not self.is_leaf:
                self.expanded = not self.expanded
                self.update_expanded_icon()
        else:
            return key


class RPQ_TreeNode(ur.TreeNode):
    def __init__(self, parent_query, key, parent=None):
        value = parent_query[key]
        self.parent_query = parent_query
        self._prev_sibling = None
        self._next_sibling = None
        super().__init__(value, parent=parent, key=key)


    def next_sibling(self):
        if self._next_sibling is False:
            return None
        if self._next_sibling:
            return self._next_sibling
        next_key_idx = self.parent_query.keys().index(self.get_key()) + 1
        if next_key_idx < len(self.parent_query.keys()):
            #log.debug(next_key_idx)
            #log.debug(self.get_key())
            #log.debug(self.parent_query.keys())
            key = self.parent_query.keys()[next_key_idx]
            self._next_sibling = RPQ_Node(self.parent_query, key,
                                          self.get_parent())
            self._next_sibling._prev_sibling = self
            return self._next_sibling


    def prev_sibling(self):
        if self._prev_sibling is False:
            return None
        if self._prev_sibling:
            return self._prev_sibling
        next_key_idx = self.parent_query.keys().index(self.get_key()) - 1
        if (next_key_idx + 1):
            #log.debug(next_key_idx)
            #log.debug(self.get_key())
            #log.debug(self.parent_query.keys())
            key = self.parent_query.keys()[next_key_idx]
            self._prev_sibling = RPQ_Node(self.parent_query, key,
                                          self.get_parent())
            self._prev_sibling._next_sibling = self
            return self._prev_sibling


    def load_widget(self):
        return RPQ_NodeText(self)


class RPQ_ParentNode(RPQ_TreeNode, ur.ParentNode):
    def load_child_keys(self):
        return self.parent_query.child_query(self.get_key()).keys()


    def load_child_node(self, key):
        return RPQ_Node(self.parent_query.child_query(self.get_key()),
                        key, self)


def RPQ_Node(parent_query, key, parent):
    if len(parent_query.child_query(key)):
        return RPQ_ParentNode(parent_query, key, parent)
    else:
        return RPQ_TreeNode(parent_query, key, parent)


class ClassView(ur.TreeListBox):
    def __init__(self, window, rpquery):
        first_node = RPQ_Node(rpquery, rpquery.keys()[0], None)
        super().__init__(ur.TreeWalker(first_node))
        self.window = window


    def keypress(self, size, key):
        if key == "enter":
            selected = self.focus.get_node().get_key()
            self.window.load_instances(selected)
        elif (res := super().keypress(size, key)):
            return res


class InstanceView(ur.TreeListBox):
    def __init__(self, window, pl):
        super().__init__(ur.TreeWalker(ur.TreeNode(None, key="")))
        self.window = window
        self.pl = pl
        self.i_class = None
        self.rpquery = None


    def keypress(self, size, key):
        if self.focus.get_node().get_value():
            sel_type = self.focus.get_node().get_value().type
            #log.debug(sel_type)
            #log.debug(instance_ops.keys())
            if (operation := instance_ops.get(sel_type, {}).get(key)):
                selected = self.focus.get_node().get_key()
                key_dict = {"key": selected}
                log.debug(f"{selected} {key} {operation}")
                mixed_query(self.pl, fill_query(operation, key_dict))
                self.window.frames['now'].reload()
                return
        if (res := super().keypress(size, key)):
            return res


    def new_tree(self, parent=None, query=None):
        if query is None:
            return
        if parent:
            self.parent = parent
        self.rpquery = query.copy(self.parent)
        if self.rpquery:
            first_node = RPQ_Node(self.rpquery, self.rpquery.keys()[0], None)
            self.body = ur.TreeWalker(first_node)


    def new_view(self, view):
        self.instance_view = view
        self.new_tree()


class ViewList(ur.ListBox):
    def __init__(self, window, pl, root_class=RDFS.Resource):
        walker = ur.SimpleFocusListWalker([])
        super().__init__(walker)
        self.pl = pl
        self.load_views(root_class)
        self.window = window


    def keypress(self, size, key):
        if key == "enter":
            self.window.load_view(tree_views[self.selected()]['query'])
        elif (res := super().keypress(size, key)):
            return res


    def load_views(self, leaf_class):
        #log.debug(leaf_class)
        classes = all_classes(self.pl, leaf_class)
        log.debug(classes)
        views = []
        for rdfs_class in classes:
            for view_name, view in tree_views.items():
                if str(view['root']) == rdfs_class:
                    views.append(view_name)
        #log.debug(views)
        self.body = ur.SimpleFocusListWalker(
            [ur.SelectableIcon(view) for view in views]
        )


    def selected(self):
        return self.focus.get_text()[0]


class Window(ur.WidgetWrap):
    def __init__(self, pl, rpq, update_rate=5):
        #class_root = RDF_ParentNode(pl, RDFS.Resource, None, **class_hierarchy)
        classtreewin = ClassView(self, class_hierarchy)

        instancetreewin = InstanceView(self, pl)

        instancelistwin = ViewList(self, pl)

        operationgrid = ur.WidgetPlaceholder(ur.SolidFill('.'))

        operationview = MpdPlayer(self.format_track, log=log)

        top_frame = ur.Columns([classtreewin, instancelistwin, operationgrid])
        bottom_frame = ur.Columns([instancetreewin, operationview])
        pile = ur.Pile([top_frame, bottom_frame])

        self.update_rate = update_rate
        self.frames = {
            "class": classtreewin,
            "browse": instancetreewin,
            "view": instancelistwin,
            "ops": operationgrid,
            "now": operationview
        }
        self.pl = pl
        self.rpq = rpq
        ur.WidgetWrap.__init__(self, pile)


    def keypress(self, size, key):
        if key in ['esc', 'q', 'Q']: raise ur.ExitMainLoop()
        if (key := self.__super.keypress(size, key)):
            key_list = key.split(' ')
            if key_list[0] == 'shift':
                if key_list[1] in ['up', 'down', 'left', 'right']:
                    self.focus_frame(key_list[1])
                    return None
            log.info(f'size:{size}, key:{key_list},'
                      f' focus:{self.active_frame()[1]}')


    def focus_frame(self, direction):
        if direction == "down":
            if self._w.focus_position == 0:
                self._w.focus_position = 1
        elif direction == "up":
            if self._w.focus_position == 1:
                self._w.focus_position = 0
        elif direction == "left":
            if self._w.focus.focus_position > 0:
                self._w.focus.focus_position -= 1
        else:
            if self._w.focus.focus_position < (len(self._w.focus.contents) - 1):
                self._w.focus.focus_position += 1


    def active_frame(self):
        if self._w.focus_position == 0:
            if self._w.focus.focus_position == 0:
                return self.frames["class"], "class"
            else:
                return self.frames["view"], "view"
        else:
            return self.frames["browse"], "browse"


    def load_instances(self, sel_class):
        self.frames["view"].load_views(sel_class)
        log.debug(self.frames["view"].selected())
        view = self.rpq.querylist(
            tree_views[self.frames["view"].selected()]['query']
        )
        log.debug(view)
        self.frames["browse"].new_tree(sel_class, view)


    def load_view(self, sel_view):
        view_query = self.rpq.querylist(sel_view)
        self.frames["browse"].new_tree(query=view_query)
        log.debug(view_query)


    def format_track(self, dictlike):
        return {
            'key': dictlike.get('id', ""),
            'track': dictlike.get('title', ""),
            'artist': dictlike.get('artist', ""),
            'album': dictlike.get('album', ""),
            'year': dictlike.get('date', "")
        }


    def update_dynamic(self, main_loop, *args):
        self.frames['now'].reload_screen()
        main_loop.set_alarm_in(self.update_rate, self.update_dynamic)


def unhandled_input(k):
    log.warning(k)


def doubletree(rpq):
    pl = Prolog()
    pl.consult('init.pl')

    try:
        window = Window(pl, rpq, update_rate=1)
        event_loop = ur.MainLoop(window, palette, unhandled_input=unhandled_input)
        event_loop.set_alarm_in(1, window.update_dynamic)
        event_loop.run()
    except PrologError as e:
        traceback.print_exc()
        query = str(e).split('Returned:')[1].split('string(b"')[1].split('"')[0]
        print(query)


if __name__ == "__main__":
    log = logging.getLogger('doubletree')
    log.setLevel(logging.DEBUG)
    log_handler = logging.FileHandler('dbltree.log', encoding='utf-8')
    log_handler.setLevel(logging.DEBUG)

    log_handler.setFormatter(LogFormatter())
    log.addHandler(log_handler)

    log.info(f"\n\t\tDoubletree {datetime.now()}")
    rpq = RPQ('init.pl', log=log)

    class_hierarchy = rpq.query(
            'ChildClass',
            f"rdf(ChildClass, '{RDFS.subClassOf}', ParentClass), "
            'xcat_label(ChildClass, Label)',
            '{Label}',
            ('ParentClass', RDFS.Resource),
            recursive=True)

    doubletree(rpq)
