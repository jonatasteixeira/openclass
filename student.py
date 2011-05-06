#!/usr/bin/python
"""
OpenClass student module.

Copyright, (C) Eugeni Dodonov <eugeni@dodonov.net>, 2008-2011

Distributed under GPLv2 license, please see COPYING for details.

"""

import sys
import traceback
import time

from multiprocessing import Queue

import socket
import SocketServer
import struct

import os
import logging
import gtk
import gtk.glade
import pygtk
import gobject
from gtk import gdk

from threading import Thread
import thread
import socket
import traceback
import time
import pynotify

import urllib, urllib2

import gettext
import __builtin__
__builtin__._ = gettext.gettext

from openclass import network, protocol, screen

DEBUG=False

# configuracoes globais
commands = None
iface_selected = 0

class Student:
    selected_machines = 0
    """Teacher GUI main class"""
    def __init__(self, guifile, max_missed_commands=30):
        """Initializes the interface"""
        # colors
        self.color_normal = gtk.gdk.color_parse("#99BFEA")
        self.color_active = gtk.gdk.color_parse("#FFBBFF")
        self.color_background = gtk.gdk.color_parse("#FFFFFF")

        self.window = gtk.Window()
        self.window.set_title("OpenClass student")
        self.window.set_default_size(640, 480)
        self.window.connect('delete-event', self.quit)

        self.main_vbox = gtk.VBox()
        self.window.add(self.main_vbox)

        # login
        hbox = gtk.HBox()
        hbox.pack_start(gtk.Label(_("Your name:")), False, False)
        self.name_label = gtk.Label(_("Not logged in"))
        hbox.pack_start(self.name_label, False, False)
        self.login_button = gtk.Button(_("Click here to login"))
        self.login_button.connect('clicked', self.login)
        hbox.pack_start(self.login_button, False, False)
        self.main_vbox.pack_start(hbox, False, False)

        # teacher
        hbox = gtk.HBox()
        hbox.pack_start(gtk.Label(_("Current teacher:")), False, False)
        self.teacher_label = gtk.Label(_("No teacher found"))
        hbox.pack_start(self.teacher_label, False, False)
        self.leave_button = gtk.Button(_("Click here to leave this class"))
        self.leave_button.connect('clicked', self.leave_class)
        self.leave_button.set_sensitive(False)
        hbox.pack_start(self.leave_button, False, False)
        self.main_vbox.pack_start(hbox, False, False)

        self.teacher_view = gtk.Label()
        self.main_vbox.pack_start(self.teacher_view)

        self.window.show_all()

        # tooltips
        self.tooltip = gtk.Tooltips()

        # protocol handler
        self.protocol = protocol.Protocol()

        # notification
        pynotify.init("OpenClass student")

        # Configura o timer
        gobject.timeout_add(1000, self.monitor_bcast)
        gobject.timeout_add(500, self.monitor_mcast)
        gobject.timeout_add(1000, self.monitor_teacher)

        self.teacher = None
        self.teacher_addr = None
        self.name = None
        self.outfile = None
        self.missed_commands = 0
        self.max_missed_commands = max_missed_commands

        # Inicializa as threads
        self.bcast = network.BcastListener(network.LISTENPORT)
        self.log( _("Starting broadcasting service.."))
        self.bcast.start()

        self.mcast = network.McastListener()

        self.screen = screen.Screen()
        self.projection_window = gtk.Window()
        self.projection_window.set_default_size(self.screen.width, self.screen.height)
        self.projection_window.show_all()
        self.projection_window.set_type_hint(gtk.gdk.WINDOW_TYPE_HINT_DESKTOP)
        self.projection_window.set_resizable(False)
        self.projection_window.set_geometry_hints(min_width = self.screen.width, min_height=self.screen.height)
        self.projection_window.set_has_frame(False)
        self.projection_window.set_decorated(False)
        self.projection_window.set_keep_above(True)
        self.projection_window.connect('delete-event', lambda *w: True)
        self.projection_window.visible = False
        self.projection_window.hide()

        # attention
        vbox = gtk.VBox()
        self.projection_window.add(vbox)
        self.attention_label = gtk.Label()
        self.attention_label.set_use_markup(True)
        vbox.pack_start(self.attention_label)

        # drawing
        self.gc = None
        self.drawing = gtk.DrawingArea()
        self.drawing.set_size_request(self.screen.width, self.screen.height)
        vbox.pack_start(self.drawing)

    def quit(self, widget, param):
        """Main window was closed"""
        gtk.main_quit()
        self.bcast.actions.put(1)
        self.mcast.actions.put(1)
        sys.exit(0)

    def leave_class(self, widget):
        """Leave a class"""
        pass

    def ask_attention(self, message=_("Teacher asked you for attention")):
        """Asks for attention"""
        self.drawing.set_visible(False)
        self.attention_label.set_markup("<big><b>%s</b></big>" % message)
        self.attention_label.set_visible(True)
        self.projection_window.fullscreen()
        self.projection_window.stick()
        self.projection_window.show_all()

    def start_projection(self):
        """Starts screen projection"""
        self.projection_window.visible = True
        self.attention_label.set_visible(True)
        self.projection_window.fullscreen()
        self.projection_window.stick()
        self.projection_window.show_all()
        self.attention_label.set_visible(False)
        self.drawing.set_visible(True)

    def noop(self):
        """Back to noop state"""
        self.projection_window.visible = False
        self.projection_window.hide()

    def login(self, widget):
        """Asks student to login"""
        dialog = gtk.Dialog(_("Login"), self.window, 0,
                (gtk.STOCK_OK, gtk.RESPONSE_OK,
                gtk.STOCK_CANCEL, gtk.RESPONSE_CANCEL))
        dialogLabel = gtk.Label(_("Please login"))
        dialog.vbox.add(dialogLabel)
        dialog.vbox.set_border_width(8)
        hbox = gtk.HBox()
        login = gtk.Label(_("Your name:"))
        hbox.pack_start(login)
        entry_login = gtk.Entry()
        entry_login.set_text(_("Meu nome"))
        hbox.pack_start(entry_login)
        dialog.vbox.pack_start(hbox)
        dialog.show_all()
        response = dialog.run()
        if response == gtk.RESPONSE_OK:
            self.name = entry_login.get_text()
            print "Login: %s" % self.name
            self.name_label.set_text(self.name)
            self.login_button.set_label(_("Login as different user"))

            # start threads
            self.mcast.start()
            dialog.destroy()
        else:
            dialog.destroy()

    def monitor_teacher(self):
        """Periodically checks for teacher commands"""
        if self.teacher_addr:
            # connect to teacher for instructions
            command, params = self.send_command("actions")
            if command == protocol.ACTION_PROJECTION:
                print "Projecting"
                self.start_projection()
            elif command == protocol.ACTION_ATTENTION:
                print "Attention!"
                self.ask_attention()
                print params
            elif command == protocol.ACTION_MSG:
                print "Message: %s" % params
                self.show_message(params)
                self.noop()
            elif command == protocol.ACTION_NOOP:
                print "Stopping everything"
                self.noop()
            else:
                # TODO: count for missed commands, and if exceedes threshold, reset to NOOP and leave teacher
                print "Unknown command %s" % command
                self.missed_commands += 1
                if self.missed_commands > self.max_missed_commands:
                    print "Too many missing commands, leaving this teacher"
                    self.noop()
                    self.teacher = None
                    self.teacher_addr = None
                    self.teacher_label.set_text(_("No teacher found"))
                    print "Unable to talk to teacher: %s" % sys.exc_value
        gobject.timeout_add(1000, self.monitor_teacher)

    def show_message(self, message):
        """Shows a message to student"""
        n = pynotify.Notification(_("Message received from teacher"), message)
        n.set_timeout(0)
        n.show()
        return

    def send_command(self, command, params={}, teacher=None):
        """Sends a command to teacher"""
        if not teacher:
            teacher = self.teacher_addr
        if not teacher:
            print "Error: no teacher yet!"
            return
        if not self.name:
            print "Error: not logged in yet!"
            return
        # TODO: proper user-agent
        url = "http://%s:%d/%s" % (teacher, network.LISTENPORT, command)
        if params:
            url += "?%s" % urllib.urlencode(params)
        headers = {'User-Agent': 'openclass'}

        command = None
        params = None
        try:
            req = urllib2.Request(url, None, headers)
            response = urllib2.urlopen(req)
            ret = response.read()
            try:
                command, params = ret.split(" ", 1)
            except:
                command = ret
                params = None
        except:
            # something went wrong, disconnect
            self.teacher = None
            self.teacher_addr = None
            print "Unable to talk to teacher: %s" % sys.exc_value
        return command, params

    def monitor_mcast(self):
        """Monitor for multicast messages"""
        while not self.mcast.messages.empty():
            message = self.mcast.messages.get()
            screen_width, screen_height, pos_x, pos_y, step_x, step_y, img = self.protocol.unpack_chunk(message)
            print "Received image at %dx%d-%dx%d" % (pos_x, pos_y, step_x, step_y)
            try:
                loader = gdk.PixbufLoader(image_type="jpeg")
                loader.write(img)
                loader.close()
                pb = loader.get_pixbuf()

                gc = self.drawing.get_style().fg_gc[gtk.STATE_NORMAL]
                self.drawing.window.draw_pixbuf(gc, pb, 0, 0, pos_x, pos_y, step_x, step_y)
            except:
                traceback.print_exc()

        gobject.timeout_add(1000, self.monitor_mcast)

    def monitor_bcast(self):
        """Monitors broadcast teacher status"""
        if self.bcast.has_msgs():
            data, source = self.bcast.get_msg()
            # if there is an announce, but we are not yet logged in, skip
            if not self.name:
                print "ERROR: found teacher, but not yet logged in!"
            else:
                msg = self.protocol.parse_header(data)
                name, flags = self.protocol.parse_announce(msg)
                # TODO: support multiple teachers
                if not self.teacher:
                    # register on teacher
                    ret, params = self.send_command("register", {"name": self.name}, teacher=source)
                    if ret == "registered":
                        # registered successfully
                        self.teacher = name
                        self.teacher_addr = source
                        self.teacher_label.set_text("%s (%s)" % (name, source))
                    elif ret == "rejected":
                        print "rejected by teacher"
                    else:
                        print "Unknown answer: %s" % ret

                elif self.teacher != name:
                    print "ERROR: Multiple teachers not yet supported"
                else:
                    # same teacher
                    pass
        gobject.timeout_add(1000, self.monitor_bcast)

    def log(self, text):
        """Logs something somewhere"""
        print text

if __name__ == "__main__":
    # configura o timeout padrao para sockets
    socket.setdefaulttimeout(5)
    # Atualizando a lista de interfaces
    gtk.gdk.threads_init()
    gtk.gdk.threads_enter()

    print _("Starting GUI..")
    gui = Student("iface/student.glade")
    try:
        gui.log(_("\nWelcome to OpenClass Student!!\n\n"))
        gtk.main()
        gtk.gdk.threads_leave()
    except:
        print "exiting.."
        sys.exit()
