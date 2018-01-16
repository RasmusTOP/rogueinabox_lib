#Copyright (C) 2017 Andrea Asperti, Carlo De Pieri, Gianmaria Pedrini
#
#This file is part of Rogueinabox.
#
#Rogueinabox is free software: you can redistribute it and/or modify
#it under the terms of the GNU General Public License as published by
#the Free Software Foundation, either version 3 of the License, or
#(at your option) any later version.
#
#Rogueinabox is distributed in the hope that it will be useful,
#but WITHOUT ANY WARRANTY; without even the implied warranty of
#MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#GNU General Public License for more details.
#
#You should have received a copy of the GNU General Public License
#along with this program.  If not, see <http://www.gnu.org/licenses/>.

import sys
import time
import os
import fcntl
import pty
import signal
import shlex
import pyte
import re
import numpy as np
import itertools
import scipy
import copy

from parser import RogueParser
from evaluator import RogueEvaluator

class Terminal:
    def __init__(self, columns, lines):
        self.screen = pyte.DiffScreen(columns, lines)
        self.stream = pyte.ByteStream()
        self.stream.attach(self.screen)

    def feed(self, data):
        self.stream.feed(data)

    def read(self):
        return self.screen.display

def open_terminal(command="bash", columns=80, lines=24):
    p_pid, master_fd = pty.fork()
    if p_pid == 0:  # Child.
        path, *args = shlex.split(command)
        args = [path] + args
        env = dict(TERM="linux", LC_ALL="en_GB.UTF-8",
                   COLUMNS=str(columns), LINES=str(lines))
        try:
            os.execvpe(path, args, env)
        except FileNotFoundError:
            print("Could not find the executable in %s. Press any key to exit." % path)
            exit()

    # set non blocking read
    flag = fcntl.fcntl(master_fd, fcntl.F_GETFD)
    fcntl.fcntl(master_fd, fcntl.F_SETFL, flag | os.O_NONBLOCK)
    # File-like object for I/O with the child process aka command.
    p_out = os.fdopen(master_fd, "w+b", 0)
    return Terminal(columns, lines), p_pid, p_out


class RogueBox:
    @staticmethod
    def get_actions():
        """return the list of actions"""
        # h, j, k, l: ortogonal moves
        # y, u, b, n: diagonal moves
        # >: go downstairs
        # return ['h', 'j', 'k', 'l', '>', 'y', 'u', 'b', 'n']
        return ['h', 'j', 'k', 'l', '>']
    
    """Start a rogue game and expose interface to communicate with it"""
    def __init__(self, game_exe_path="rogue", mem_size=-1):
        self.rogue_path = game_exe_path
        self.memory_size = mem_size
        self.parser = RogueParser()
        self.evaluator = RogueEvaluator()
        self.terminal, self.pid, self.pipe = open_terminal(command=self.rogue_path)
        # our internal screen is list of lines, each line is a string
        # can be indexed as a 24x80 matrix
        self.screen = []
        self.stairs_pos = None
        self.past_positions = []
        self.frame_info = []
        
        self.parser.reset()

        if not self.is_running():
            print("Could not find the executable in %s." % self.rogue_path)
            exit()
        
        # wait until the rogue spawns
        self.screen = self.get_empty_screen()
        while  not ('Hp:' in self.screen[-1]):
            self._update_screen()

        self._update_screen()
        self.frame_info.append( self.parser.parse_screen( self.screen ) )
        self.parse_statusbar_re = self._compile_statusbar_re()


    @staticmethod
    def _compile_statusbar_re():
        parse_statusbar_re = re.compile(r"""
                Level:\s*(?P<dungeon_level>\d*)\s*
                Gold:\s*(?P<gold>\d*)\s*
                Hp:\s*(?P<current_hp>\d*)\((?P<max_hp>\d*)\)\s*
                Str:\s*(?P<current_strength>\d*)\((?P<max_strength>\d*)\)\s*
                Arm:\s*(?P<armor>\d*)\s*
                Exp:\s*(?P<exp_level>\d*)/(?P<tot_exp>\d*)\s*
                (?P<status>(Hungry|Weak|Faint)?)\s*
                (Cmd:\s*(?P<command_count>\d*))?""", re.VERBOSE)
        return parse_statusbar_re



    def _update_screen(self):
        """update the virtual screen and the class variable"""
        update = self.pipe.read(65536)
        if update:
            self.terminal.feed(update)
            self.screen = self.terminal.read()
            
    def get_empty_screen(self):
        screen = list()
        for row in range(24):
            value = ""
            for col in range(80):
                value += " "
            screen.append(value)
        return screen


    @property
    def player_pos(self):
        return self.frame_info[-1].get_list_of_positions_by_tile("@")[0]

    # get info methods

    def get_actions(self):
        """return the list of actions"""
        actions = ['h', 'j', 'k', 'l', '>']
        #actions = ['h', 'j', 'k', 'l']
        return actions

    def get_legal_actions(self):
        actions = []
        row = self.player_pos[0]
        column = self.player_pos[1]
        if self.screen[row-1][column] not in '-| ':
            actions += ['k']
        if self.screen[row+1][column] not in '-| ':
            actions += ['j']
        if self.screen[row][column-1] not in '-| ':
            actions += ['h']
        if self.screen[row][column+1] not in '-| ':
            actions += ['l']
        if self.player_pos == self.stairs_pos:
            actions += ['>']
        return actions

    def print_screen(self):
        """print the current screen"""
        print(*self.screen, sep='\n')

    def get_screen(self):
        """return the screen as a list of strings.
        can be treated like a 24x80 matrix of characters (screen[17][42])"""
        return self.screen

    def get_stat(self, stat):
        """Get the chosen 'stat' from the current screen as a string. Available stats:
        dungeon_level, gold, current_hp, max_hp, 
        current_strength, max_strength, armor, exp_level, tot_exp """
        return self._get_stat_from_screen(stat, self.screen)

    def _get_stat_from_screen(self, stat, screen):
        """Get the chosen 'stat' from the given 'screen' as a string. Available stats:
        dungeon_level, gold, current_hp, max_hp, 
        current_strength, max_strength, armor, exp_level, tot_exp """
        parsed_status_bar = self.parse_statusbar_re.match(screen[-1])
        answer = None
        if parsed_status_bar:
            answer = parsed_status_bar.groupdict()[stat]
        return answer


    def get_screen_string(self):
        """return the screen as a single string with \n at EOL"""
        out = ""
        for line in self.screen:
            out += line
            out += '\n'
        return out

    def game_over(self, screen=None):
        """check if we are at the game over screen (tombstone)"""
        if not screen:
            screen = self.screen
        # look for tombstone
        for line in screen:
            if '_______)' in line or 'You quit' in line:
                return True
        return False

    def print_screen(self):
        """print the current screen"""
        print(*self.screen, sep='\n')


    def is_map_view(self, screen):
        """return True if the current screen is the dungeon map, False otherwise"""
        statusbar = screen[-1]
        parsed_statusbar = self.parse_statusbar_re.match(statusbar)
        if parsed_statusbar:
            # if there is a status bar
            return True
        else:
            return False

    def is_running(self):
        """check if the rogue process exited"""
        try:
            pid, status = os.waitpid(self.pid, os.WNOHANG)
        except OSError:
            return False
        if pid == 0:
            return True
        else:
            return False

    def currently_in_corridor(self):
        info = self.frame_info[-1]
        return info.get_list_of_positions_by_tile("@")[0] in info.get_list_of_positions_by_tile("#")

    def currently_in_door(self):
        info = self.frame_info[-1]
        return info.get_list_of_positions_by_tile("@")[0] in info.get_list_of_positions_by_tile("+")
    

    def _dismiss_message(self):
        """dismiss a rogue status message.
        call it once, because it will call itself again until
        all messages are dismissed (through send_command())"""
        messagebar = self.screen[0]
        if "ore--" in messagebar:
            # press space
            self.send_command(' ')
        elif "all it" in messagebar:
            # press esc
            self.send_command('\e')

    def _need_to_dismiss(self):
        """check if there are status messages that need to be dismissed"""
        messagebar = self.screen[0]
        if "all it" in messagebar or "ore--" in messagebar:
            return True
        else:
            return False

    def _update_stairs_pos(self, old_screen, new_screen):
        old_statusbar = old_screen[-1]
        new_statusbar = new_screen[-1]
        parsed_old_statusbar = self.parse_statusbar_re.match(old_statusbar)
        parsed_new_statusbar = self.parse_statusbar_re.match(new_statusbar)
        if parsed_old_statusbar and parsed_new_statusbar:
            old_statusbar_infos = parsed_old_statusbar.groupdict()
            new_statusbar_infos = parsed_new_statusbar.groupdict()
            if new_statusbar_infos["dungeon_level"] > old_statusbar_infos["dungeon_level"]:
                #changed floor, reset stairsposition to unknown
                self.stairs_pos = None
            # search the screen for visible stairs
            for i, j in itertools.product(range(1, 23), range(80)):
                pixel = new_screen[i][j]
                if pixel == "%":
                    self.stairs_pos = (i, j)


    def _update_past_positions(self, old_screen, new_screen):
        old_statusbar = old_screen[-1]
        new_statusbar = new_screen[-1]
        parsed_old_statusbar = self.parse_statusbar_re.match(old_statusbar)
        parsed_new_statusbar = self.parse_statusbar_re.match(new_statusbar)
        if parsed_old_statusbar and parsed_new_statusbar:
            old_statusbar_infos = parsed_old_statusbar.groupdict()
            new_statusbar_infos = parsed_new_statusbar.groupdict()
            if int(new_statusbar_infos["dungeon_level"]) > int(old_statusbar_infos["dungeon_level"]):
                self.past_positions = []
            elif self.memory_size > 0 and len(self.past_positions) > self.memory_size:
                self.past_positions.pop(0)
        self.past_positions.append(self.player_pos)


    def count_passables(self):
        """Count the passable tiles in the current screen and returns it as an int."""
        return self._count_passables_in_screen(self.screen)


    def _count_passables_in_screen(self, screen):
        """Count the passable tiles in a given 'screen' (24*80 matrix) and returns it as an int."""
        passables = 0
        impassable_pixels =  '|- '
        for line in screen:
            for pixel in line:
                if pixel not in impassable_pixels:
                    passables += 1
        return passables


    def reset(self):
        """kill and restart the rogue process"""
        if self.is_running():
            os.kill(self.pid, signal.SIGTERM)
            # wait the process so it doesnt became a zombie
            os.waitpid(self.pid, 0)
        try:
            self.pipe.close()
        except:
            pass
        try:
            self.__init__(self.rogue_path, self.memory_size)
        except:
            self.reset()


    def quit_the_game(self):
        """Send the keystroke needed to quit the game."""
        self.send_command('Q')
        self.send_command('y')
        self.send_command('\n')


    def get_last_frame(self):
        return self.frame_info[-1]

    # interact with rogue methods
    def send_command(self, command):
        """send a command to rogue"""
        old_screen = self.screen[:]
        lvl = self.get_stat("dungeon_level")
        self.pipe.write(command.encode())
        if command in self.get_actions():
            self.pipe.write('\x12'.encode())

        if self.get_stat("command_count"):
            new_screen = old_screen
            while old_screen[-1] == new_screen[-1]: # after a command execution, the new screen is always different from the old one
                self._update_screen()
                while self._need_to_dismiss(): # will dismiss all upcoming messages
                    self._dismiss_message()
                    self._update_screen()
                new_screen = self.screen
        else:
            time.sleep(0.01)
            self._update_screen()
            if self._need_to_dismiss():
                # will dismiss all upcoming messages,
                # because dismiss_message() calls send_command() again
                self._dismiss_message()
            new_screen = self.screen[:]


        terminal = self.game_over(new_screen)
        new_lvl = self.get_stat("dungeon_level")

        self._update_stairs_pos(old_screen, new_screen)
        #self._update_player_pos()
        self._update_past_positions(old_screen, new_screen)

        if not terminal:
            if not self.frame_info:
                self.frame_info.append( self.parser.parse_screen( old_screen ) )
            self.frame_info.append( self.parser.parse_screen( new_screen ) )
            
            #self.step_count += 1
            #lose = self.step_count > self.max_step_count or self.state_generator.need_reset
        
        #win = self.reward_generator.goal_achieved
        #if win or lose:
            #self.evaluator.add( info = self.frame_info[-1], reward = self.episode_reward, has_won = win, step = self.step_count )
        #return self.reward, self.state, win or lose

        return (old_screen, new_screen), terminal
