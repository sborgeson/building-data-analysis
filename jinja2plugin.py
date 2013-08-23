# -*- coding: utf-8 -*-
import cherrypy
from cherrypy.process import plugins

__all__ = ['Jinja2TemplatePlugin']

class Jinja2TemplatePlugin(plugins.SimplePlugin):
    """A WSPBus plugin that manages Jinja2 templates"""

    def __init__(self, bus, env):
        plugins.SimplePlugin.__init__(self, bus)
        self.env = env

    def start(self):
        """
        Called when the engine starts. 
        """
        self.bus.log('Setting up Jinja2 resources')
        self.bus.subscribe("lookup-template", self.get_template)

    def stop(self):
        """
        Called when the engine stops. 
        """
        self.bus.log('Freeing up Mako resources')
        self.bus.unsubscribe("lookup-template", self.get_template)
        self.env = None

    def get_template(self, name):
        """
        Returns Jinja2's template by name.

        Used as follow:
        >>> template = cherrypy.engine.publish('lookup-template', 'index.html').pop()
        """
        return self.env.get_template(name)
        