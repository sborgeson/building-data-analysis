# -*- coding: utf-8 -*-
import cherrypy
from cherrypy.process import plugins

__all__ = ['Jinja2TemplatePlugin', 'Jinja2Tool']


class Jinja2Tool(cherrypy.Tool):
  def __init__(self):
    cherrypy.Tool.__init__(self, 'before_finalize',
                           self._render,
                           priority=10)
      
  def _render(self, template=None, debug=False):
    """
    Applied once your page handler has been called. It
    looks up the template from the various template directories
    defined in the Jinja2 plugin then renders it with
    whatever dictionary the page handler returned.
    """
    if cherrypy.response.status > 399:
      return

    # retrieve the data returned by the handler
    data = cherrypy.response.body or {}
    template = cherrypy.engine.publish("lookup-template", template).pop()

    if template and isinstance(data, dict):
      cherrypy.response.body = template.render(**data)


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
        