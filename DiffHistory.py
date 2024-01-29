import sublime
import sublime_plugin
from sublime_plugin import EventListener
import time
import datetime
import os
import json
import concurrent.futures
import DiffHistory.diff_match_patch as dmp_module

is_browsing_history = False

class TakeSnapshot(EventListener):

    def __init__(self):
        self.last_time = time.time()
        self.file_being_renamed = None
        self.old_name = None
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=10) 

    def on_modified(self, view):
        self.executor.submit(self.take_snapshot, view)

    def on_post_save_async(self, view):
        self.executor.submit(self.take_snapshot, view)

    def take_snapshot(self, view):
        global is_browsing_history
        if is_browsing_history:
            return
        filename = view.file_name()
        if not filename or not view or (filename.endswith('.diff')):
            return
        now = time.time()
        if now - self.last_time < 5:
            return
        self.last_time = now
        take_snapshot(
            filename, 
            view.substr(sublime.Region(0, view.size()))
            )

    def on_window_command(self, window, command_name, args):
        """
        Change the rename functionality here 
        instead of using built-in events. 
        """
        if command_name == 'rename_path':
            old_name = args['paths'][0]
            for v in window.views():
                if v.file_name() == old_name:
                    self.old_name = old_name
                    self.view_being_renamed = v
            return

            if self.old_name:
                new_filename = self.view_being_renamed.file_name()
                old_history_file = os.path.join(
                    os.path.dirname(self.old_name), 
                    '_diff',
                    os.path.basename(self.old_name) + '.diff')
                
                if os.path.exists(old_history_file):
                    if not os.path.exists(os.path.join(
                            os.path.dirname(new_filename),
                            '_diff'
                            )):
                        os.mkdir(
                            os.path.join(
                                os.path.dirname(new_filename), 
                                '_diff'))
                    new_history_file = os.path.join(
                        os.path.dirname(new_filename),
                        '_diff',
                        os.path.basename(new_filename) + '.diff')
                    os.rename(old_history_file, new_history_file)
            self.old_name = None
            self.view_being_renamed = None

class BrowseHistoryCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        
        if self.view.file_name():

            global is_browsing_history
            is_browsing_history = True
            
            take_snapshot(
                self.view.file_name(), 
                self.view.substr(sublime.Region(0, self.view.size()))
                )

            new_history = get_history(self.view.file_name())                
            if not new_history:
                return None
            
            ts_format = '%a., %b. %d, %Y, %I:%M %p'
            string_timestamps = [
                datetime.datetime.fromtimestamp(int(i)).strftime(ts_format) for i in 
                sorted(new_history.keys(), reverse=True)
                ]

            self.view.window().show_quick_panel(
                string_timestamps,
                self.done,
                on_highlight=self.show_state,
                )

    def done(self, index):
        global is_browsing_history
        is_browsing_history=False
        self.view.erase_regions('dmp_add')
        deleted_regions = self.view.get_regions('dmp_del')
        for r in deleted_regions:
            self.view.run_command('diff_match_patch_replace', {
                'start' : r.a,
                'end' :r.b,
                'replacement_text' :''
                })
        self.view.erase_regions('dmp_del')

    def show_state(self, distance_back):
        text, added_ranges, deleted_ranges = apply_history_patches_with_deletions(self.view.file_name(), distance_back)
        dmp = dmp_module.diff_match_patch()
        history = get_history(self.view.file_name()) 
        timestamps = sorted(history.keys())
        for index in range(1, len(timestamps)-distance_back):
            next_patch = history[timestamps[index]]
        self.view.run_command('diff_match_patch_replace', {
            'start' : 0,
            'end' :self.view.size(),
            'replacement_text' : text
            })
        self.view.erase_regions('dmp_add')
        self.view.erase_regions('dmp_del')
        self.view.add_regions('dmp_add', 
            [sublime.Region(r[0], r[1]) for r in added_ranges],
            scope="region.greenish")
        self.view.add_regions('dmp_del', 
            [sublime.Region(r[0], r[1]) for r in deleted_ranges],
            scope="region.redish")

# class ShowTimeWrittenCommand(sublime_plugin.TextCommand):

#     def run(self, edit):
        
#         if self.view.file_name():
#             take_snapshot(
#                 self.view.file_name(), 
#                 self.view.substr(sublime.Region(0, self.view.size())))
#             new_history = get_history(self.view.file_name())                
#             if not new_history:
#                 return None
#             ts_format = '%a., %b. %d, %Y, %I:%M %p'
#             index = 1
#             current_contents = self.view.substr(sublime.Region(0, self.view.size()))
#             keys = new_history.keys()
#             dmp = dmp_module.diff_match_patch()
#             changed_sections = []
#             for c in changed_sections:
#                 if c in current_contents:
#                     self.view.add_regions(
#                         'diff_history',
#                         [sublime.Region(
#                             current_contents.find(c),
#                             current_contents.find(c) + len(c)
#                         )])
#                     return

def take_snapshot(filename, contents):

    dmp = dmp_module.diff_match_patch()

    if not os.path.exists(os.path.join(os.path.dirname(filename), '_diff')):
        os.mkdir(os.path.join(os.path.dirname(filename), '_diff'))
    
    history_file = os.path.join(
        os.path.dirname(filename), 
        '_diff',
        os.path.basename(filename) + '.diff')
    
    file_history = get_history(filename)
    if not file_history:
        file_history = { int(time.time()) : contents }
        with open( history_file, "w") as f:
            f.write(json.dumps(file_history))
    else:
        latest_history = apply_patches(file_history)
        if contents != latest_history:
            file_history[int(time.time())] = dmp.patch_toText(
                dmp.patch_make(
                    latest_history, 
                    contents)
                )                
            os.remove(history_file) # might prevent duplicate files on cloud storage ?
            with open(history_file, "w") as f:
                f.write(json.dumps(file_history))

def apply_history_patches_with_deletions(filename, distance_back):
    dmp = dmp_module.diff_match_patch()
    history = get_history(filename) 
    timestamps = sorted(history.keys())
    original = history[timestamps[0]]
    for index in range(1, len(timestamps)-distance_back):
        next_patch = history[timestamps[index]]        
        original = dmp.patch_apply(dmp.patch_fromText(next_patch), original)[0]
    
    added_ranges = []
    deleted_ranges = []
    next_patch = dmp.patch_fromText(next_patch)
    for patch in next_patch:
        start_offset = 0
        for diff_type, diff_text in patch.diffs:
            if diff_type == 0:
                start_offset += len(diff_text)
            if diff_type == -1:
                original = ''.join([
                    original[:start_offset+patch.start1],
                    diff_text,
                    original[start_offset+patch.start1:]
                    ])
                deleted_ranges.append((start_offset+patch.start2, start_offset+patch.start2+len(diff_text)))
                start_offset += len(diff_text)
            if diff_type == 1:
                added_ranges.append((start_offset+patch.start2, start_offset+patch.start2+len(diff_text)))

    return original, added_ranges, deleted_ranges

def apply_patches(history, distance_back=0):
    dmp = dmp_module.diff_match_patch()
    timestamps = sorted(history.keys())
    original = history[timestamps[0]]
    for index in range(1,len(timestamps)-distance_back):
        next_patch = history[timestamps[index]]
        original = dmp.patch_apply(dmp.patch_fromText(next_patch), original)[0]
    return original

def get_history(filename):
    history_file = os.path.join(
        os.path.dirname(filename), 
        '_diff', 
        os.path.basename(filename) + '.diff')
    if os.path.exists(history_file):
        with open(history_file, "r") as f:
            file_history = f.read()
        return json.loads(file_history)

class DiffMatchPatchReplace(sublime_plugin.TextCommand):

    def run(self, edit, start=0, end=0, replacement_text=''):
        self.view.replace(edit, sublime.Region(start, end), replacement_text)
