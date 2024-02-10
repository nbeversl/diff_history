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
TS_FORMAT = '%a., %b. %d, %Y, %I:%M %p'

class TakeSnapshot(EventListener):

    def __init__(self):
        self.last_time = time.time()
        self.file_being_renamed = None
        self.old_name = None
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=10) 

    def on_modified(self, view):
        if self.should_take_snapshot(view):
            self.executor.submit(self.take_snapshot, view)

    def on_post_save_async(self, view):
        self.executor.submit(self.take_snapshot, view)

    def should_take_snapshot(self, view):
        global is_browsing_history
        if is_browsing_history:
            return False
        filename = view.file_name()
        if not filename or not view or filename.endswith('.diff'):
            return False
        now = time.time()
        if now - self.last_time < 5:
            return False
        self.last_time = now
        return True

    def take_snapshot(self, view):
        print('taking snapshot')
        take_snapshot(
            view.file_name(), 
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
            if is_browsing_history:
                return
            is_browsing_history = True
            self.existing_contents = self.view.substr(sublime.Region(0, self.view.size()))
            take_snapshot(self.view.file_name(), self.existing_contents)
            self.patch_changes = build_history_patches_with_deletions(
                self.view.file_name(),
                self.view.sel()[0].a)              
            if not self.patch_changes:
                return

            string_timestamps = [
                datetime.datetime.fromtimestamp(int(i)).strftime(TS_FORMAT) for i in 
                sorted([int(i) for i in self.patch_changes.keys()], reverse=True)
                ]

            self.timestamps = sorted(self.patch_changes.keys(), reverse=True)
            self.view.window().show_quick_panel(
                string_timestamps,
                self.done,
                on_highlight=self.show_state)

    def show_state(self, distance_back):
        patch = self.patch_changes[self.timestamps[distance_back]]    
        self.view.erase_regions('dmp_add')
        self.view.erase_regions('dmp_del')

        self.view.run_command('diff_match_patch_replace', {
            'start' : 0,
            'end' :self.view.size(),
            'replacement_text' : patch['display']
            })

        for region in patch['added_ranges']:
            self.view.add_regions('dmp_add', 
                [sublime.Region(region[0], region[1])],
                scope="region.greenish")

        for region in patch['deleted_ranges']:
            self.view.add_regions('dmp_del', 
                [sublime.Region(region[0], region[1])],
                scope="region.redish")

        print(patch['approx_position'])
        self.view.show(sublime.Region(
            patch['approx_position'],
            patch['approx_position']))

    def done(self, index):
        self.view.erase_regions('dmp_add')
        if index > -1: 
            deleted_regions = self.view.get_regions('dmp_del')
            for r in deleted_regions:
                self.view.run_command('diff_match_patch_replace', {
                    'start' : r.a,
                    'end' :r.b,
                    'replacement_text' :''
                    })
        else: # escaped/cancelled
            self.view.run_command('diff_match_patch_replace', {
                'start' : 0,
                'end' :self.view.size(),
                'replacement_text' : self.existing_contents
            })
        self.view.erase_regions('dmp_del')
        self.view.erase_regions('dmp_pos')
        global is_browsing_history
        is_browsing_history=False

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

def build_history_patches_with_deletions(filename, tracked_position):

    dmp = dmp_module.diff_match_patch()
    history = get_history(filename)
    timestamps = sorted(history.keys())
    original = history[timestamps[0]]
    fully_patched_original = original
    added_ranges = []
    deleted_ranges = []
    next_patch = None
    patch_changes = {}

    for index in range(0, len(timestamps)):
        timestamp = timestamps[index]
        next_patch = history[timestamp]
        if index == 0: # first entry
            patch_changes[timestamp] = {
                'added_ranges': [(0, len(next_patch))],
                'deleted_ranges' : [],
                'state': next_patch,
                'display' : next_patch,
            }
            continue

        patch_group = dmp.patch_fromText(next_patch)
        fully_patched_original = dmp.patch_apply(
            patch_group,
            fully_patched_original)[0]
        patch_changes[timestamp] = {
            'added_ranges' : [],
            'deleted_ranges' : []
        }
        display_state_at_timestamp = fully_patched_original
        for patch in patch_group:
            start_offset = 0
            offset = 0
            offset_pos = 0
            for diff_type, diff_text in patch.diffs:
                if diff_type == 0:
                    start_offset += len(diff_text)
                start_pos = start_offset+patch.start2
                end_pos = start_pos+len(diff_text)
                if diff_type == -1:
                    display_state_at_timestamp = ''.join([
                        display_state_at_timestamp[:start_pos],
                        diff_text,
                        display_state_at_timestamp[start_pos:]
                        ])
                    patch_changes[timestamp]['deleted_ranges'].append((start_pos, end_pos))
                    offset = len(diff_text)
                    offset_pos = start_pos
                if diff_type == 1:
                    if offset > 0 and offset_pos < start_pos:
                        start_pos += offset
                    patch_changes[timestamp]['added_ranges'].append((start_pos, end_pos))

        patch_changes[timestamp]['display'] = display_state_at_timestamp

    for index in range(len(timestamps)-1, 0, -1):
        patch = patch_changes[timestamps[index]]
        for region in patch['added_ranges']:
            print(region)
            if region[1] < tracked_position:
                tracked_position += (region[1] - region[0])
        for region in patch['deleted_ranges']:
            print(region)
            if region[1] < tracked_position:
                tracked_position -= (region[1] - region[0])
        patch['approx_position'] = tracked_position

    return patch_changes

def apply_patches(history):
    dmp = dmp_module.diff_match_patch()
    timestamps = sorted(history.keys())
    original = history[timestamps[0]]
    for index in range(1,len(timestamps)):
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
