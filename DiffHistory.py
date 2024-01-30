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
            is_browsing_history = True
            
            take_snapshot(
                self.view.file_name(), 
                self.view.substr(sublime.Region(0, self.view.size())))
            self.existing_contents = self.view.substr(sublime.Region(0, self.view.size()))

            new_history = get_history(self.view.file_name())                
            if not new_history:
                return None
            string_timestamps = [
                datetime.datetime.fromtimestamp(int(i)).strftime(TS_FORMAT) for i in 
                sorted([int(i) for i in new_history.keys()], reverse=True)
                ]

            self.view.window().show_quick_panel(
                string_timestamps,
                self.done,
                on_highlight=self.show_state,
                )

    def show_state(self, distance_back):
        current_position = self.view.sel()[0].a
        patched_version, added_ranges, deleted_ranges = apply_history_patches_with_deletions(
            self.view.file_name(),
            distance_back)
        self.view.run_command('diff_match_patch_replace', {
            'start' : 0,
            'end' :self.view.size(),
            'replacement_text' : patched_version
            })
        self.view.erase_regions('dmp_add')
        self.view.erase_regions('dmp_del')
        self.view.add_regions('dmp_add',
            [sublime.Region(r[0], r[1]) for r in added_ranges],
            scope="region.greenish")
        self.view.add_regions('dmp_del', 
            [sublime.Region(r[0], r[1]) for r in deleted_ranges],
            scope="region.redish")
        if added_ranges:
            self.view.show(sublime.Region(added_ranges[0][0], added_ranges[0][1]))
        elif deleted_ranges:
            self.view.show(sublime.Region(deleted_ranges[0][0], deleted_ranges[0][1]))
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(current_position, current_position))

    def done(self, index):
        global is_browsing_history
        is_browsing_history=False
        self.view.erase_regions('dmp_add')
        if index > -1:
            deleted_regions = self.view.get_regions('dmp_del')
            for r in deleted_regions:
                self.view.run_command('diff_match_patch_replace', {
                    'start' : r.a,
                    'end' :r.b,
                    'replacement_text' :''
                    })
        else:
            self.view.run_command('diff_match_patch_replace', {
                'start' : 0,
                'end' :self.view.size(),
                'replacement_text' : self.existing_contents
            })

        self.view.erase_regions('dmp_del')

class ShowTimeWrittenCommand(sublime_plugin.TextCommand):

    def run(self, edit):
        
        if self.view.file_name():

            global is_browsing_history
            is_browsing_history = True
            
            take_snapshot(
                self.view.file_name(),
                self.view.substr(sublime.Region(0, self.view.size())))

            self.existing_contents = self.view.substr(sublime.Region(0, self.view.size()))
            text, added_ranges, deleted_ranges, position_additions, position_deletions = apply_history_patches_with_deletions(
                self.view.file_name(),
                -1,
                stop_position=self.view.sel()[0].a)
            index = 1
            position_changes = {}
            for p in position_additions:
                position_changes[p['timestamp']] = { 
                    'change': ' (added)',
                    'timestamp': datetime.datetime.fromtimestamp(int(p['timestamp'])).strftime(TS_FORMAT),
                    'position_at_timestamp' : p['position_at_timestamp'],
                    'region' : p['region'],
                    'state': p['patched_original']
                }
            for p in position_deletions:
                position_changes[p['timestamp']] = { 
                    'change': ' (deleted)',
                    'timestamp': datetime.datetime.fromtimestamp(int(p['timestamp'])).strftime(TS_FORMAT),
                    'position_at_timestamp' : p['position_at_timestamp'],
                    'region' : p['region'],
                    'state': p['patched_original']
                }
            self.position_changes = position_changes
            timestamps = sorted(list(position_changes.keys()), reverse=True)
            self.timestamps = timestamps
            items = []
            for t in timestamps:
                items.append(position_changes[t]['timestamp']+position_changes[t]['change'])
            self.view.window().show_quick_panel(
                items,
                self.done,
                on_highlight=self.show_state)

    def show_state(self, index):
        state = self.position_changes[self.timestamps[index]]
        self.view.run_command('diff_match_patch_replace', {
            'start' : 0,
            'end' :self.view.size(),
            'replacement_text' : state['state']
            })
        self.view.erase_regions('dmp_add')
        self.view.erase_regions('dmp_del')
        if state['change'] == ' (added)':
            self.view.add_regions(
                'dmp_add',
                [sublime.Region(state['region'][0], state['region'][1])],
                scope="region.greenish")
        if state['change'] == ' (deleted)':
            self.view.add_regions('dmp_del', 
            [sublime.Region(state['region'][0], state['region'][1])],
            scope="region.redish")
        self.view.show(sublime.Region(state['position_at_timestamp'], state['position_at_timestamp']))
        self.view.sel().clear()
        self.view.sel().add(sublime.Region(state['position_at_timestamp'], state['position_at_timestamp']))

    def done(self, index):
        global is_browsing_history
        is_browsing_history=False
        self.view.erase_regions('dmp_add')
        deleted_regions = self.view.get_regions('dmp_del')
        if index > -1:
            deleted_regions = self.view.get_regions('dmp_del')
            for r in deleted_regions:
                self.view.run_command('diff_match_patch_replace', {
                    'start' : r.a,
                    'end' :r.b,
                    'replacement_text' :''
                    })
        else:
            self.view.run_command('diff_match_patch_replace', {
                'start' : 0,
                'end' :self.view.size(),
                'replacement_text' : self.existing_contents
            })
        self.view.erase_regions('dmp_del')

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

def apply_history_patches_with_deletions(
    filename,
    distance_back):

    dmp = dmp_module.diff_match_patch()
    history = get_history(filename)
    timestamps = sorted(history.keys())
    original = history[timestamps[0]]
    fully_patched_original = original
    added_ranges = []
    deleted_ranges = []
    next_patch = None
    tracked_stop_position = 0
    size_change_before_stop_position = 0

    for index in range(0, len(timestamps) - distance_back):
        next_patch = history[timestamps[index]]
        if index == 0: # first entry
            fully_patched_original = next_patch
            continue
        patch_group = dmp.patch_fromText(next_patch)
        fully_patched_original = dmp.patch_apply(dmp.patch_fromText(next_patch), fully_patched_original)[0]

    if next_patch:
        if index > 0:
            next_patch = dmp.patch_fromText(next_patch)
            for patch in next_patch:
                start_offset = 0
                for diff_type, diff_text in patch.diffs:
                    if diff_type == 0:
                        start_offset += len(diff_text)
                    if diff_type == -1:
                        fully_patched_original = ''.join([
                            fully_patched_original[:start_offset+patch.start1],
                            diff_text,
                            fully_patched_original[start_offset+patch.start1:]
                            ])
                        start_pos = start_offset+patch.start1
                        end_pos = start_pos+len(diff_text)
                        deleted_ranges.append((start_pos, end_pos))
                    if diff_type == 1:
                        start_pos = start_offset+patch.start2
                        end_pos = start_pos+len(diff_text)
                        added_ranges.append((start_pos, end_pos))
        else: # first entry
            fully_patched_original = next_patch
            added_ranges.append((0, len(fully_patched_original)))
    return fully_patched_original, added_ranges, deleted_ranges


def apply_history_patches_with_deletions_at_position():
    dmp = dmp_module.diff_match_patch()
    history = get_history(filename)
    timestamps = sorted(history.keys())
    if distance_back == -1:
        distance_back = len(timestamps)
    original = history[timestamps[0]]
    fully_patched_original = original
    added_ranges = []
    deleted_ranges = []
    position_deletions = []
    position_additions = []
    next_patch = None
    tracked_stop_position = 0
    size_change_before_stop_position = 0
    if stop_position:
        tracked_stop_position = stop_position

    for index in range(0, len(timestamps) - distance_back):
        next_patch = history[timestamps[index]]
        if index == 0: # first entry
            original = next_patch
            position_additions.append({
                'timestamp' : timestamps[index],
                'position_at_timestamp' : stop_position,
                'region': (0, len(original)),
                'patched_original': original,
            })
            continue
        patch_group = dmp.patch_fromText(next_patch)
        fully_patched_original = dmp.patch_apply(dmp.patch_fromText(next_patch), fully_patched_original)[0]

        if stop_position:
            # if there is a stop position, we want only the diffs at that position, not all of them.
            for patch in patch_group:
                start_offset = 0

                # for every patch
                for diff_type, diff_text in patch.diffs:

                    # if something was altered, add its length to anything added/deleted.
                    if diff_type == 0:
                        start_offset += len(diff_text) # ?

                    # if something was deleted
                    if diff_type == -1:

                        # get the start and end positions of the patch
                        start_pos = start_offset + patch.start1
                        end_pos = start_pos + len(diff_text)

                        # if the position of interest is within the range of the patch
                        if tracked_stop_position in range(start_pos, end_pos):

                            position_deletions.append({
                                'timestamp' : timestamps[index],
                                'position_at_timestamp' : tracked_stop_position,
                                'region': (start_pos, end_pos),
                                'patched_original': ''.join([
                                        original[:start_pos+size_change_before_stop_position],
                                        diff_text,
                                        original[start_pos+size_change_before_stop_position:]
                                    ])
                                })
                        # if the insertion was done before the tracked stop position,
                        # the tracked stop position must be adjusted
                        if tracked_stop_position > end_pos:
                            tracked_stop_position -= len(diff_text)
                            size_change_before_stop_position -= len(diff_text)
                    if diff_type == 1:
                        start_pos = start_offset + patch.start2
                        end_pos = start_pos + len(diff_text)
                        if tracked_stop_position in range(start_pos, end_pos):
                            position_additions.append({
                                'timestamp' : timestamps[index],
                                'position_at_timestamp' : tracked_stop_position,
                                'region': (start_pos, end_pos),
                                'patched_original': ''.join([
                                        original[:start_pos+size_change_before_stop_position],
                                        diff_text,
                                        original[end_pos+size_change_before_stop_position:]
                                    ])
                                })
                        if tracked_stop_position > end_pos:
                            tracked_stop_position += len(diff_text)
                            size_change_before_stop_position += len(diff_text)
    return original, added_ranges, deleted_ranges, position_additions, position_deletions


def reverse_diff(diff):
    # Reverse a single diff
    return (diff[0], diff[1][::-1]) if diff[0] == 1 else (diff[0], diff[1])

def reverse_patch(patch):
    # Reverse all diffs in a patch
    return [reverse_diff(diff) for diff in patch]

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
