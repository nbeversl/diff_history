
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

def get_contents(view):
    if view != None: 
        return view.substr(sublime.Region(0, view.size()))
    return None

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

    def show_state(self, index):
        state = apply_history_patches(self.view.file_name(), index)
        self.view.run_command("select_all")
        self.view.run_command("right_delete")
        for line in state.split('\n'):
            self.view.run_command("append", {"characters": line+ "\n" })

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

def apply_history_patches(filename, distance_back):
    file_history = get_history(filename)
    distance_back = int(distance_back)
    return apply_patches(file_history, distance_back)
 
def most_recent_history(self, history):
    times = sorted(history.keys())
    return times[-1]

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

