#!/usr/bin/python

"""CherryPy server that loads and visualizes GB XML data"""
import os
import datetime, threading, random
import zipfile, shutil, re, time
import pickle
import xml

import cherrypy
import urlparse
import mimetypes
from cherrypy.lib.static import serve_file
from cherrypy.lib.static import serve_download

from jinja2        import Environment, FileSystemLoader
from jinja2support import Jinja2TemplatePlugin, Jinja2Tool

import numpy as np
import scipy.stats

import analysis
from analysis      import Building, PlotMaker
from WeatherData   import WeatherData
import GBParse
import CSVParse

code_dir = os.path.dirname(os.path.abspath(__file__))
fileLock = threading.Lock()

mimetypes.types_map[".xml"]="application/xml"

# each user session gets its own working directory, following a naming convention
def getUserDir(sId=None):
  if sId is None: sId = cherrypy.session._id
  return(os.path.join(cherrypy.config.get("work.file.dir"),sId))

# data files are standardized as GB_data.<ext> in the working dir
def getDataFile(sId=None,ext='xml'):
  return(os.path.join(getUserDir(sId),"GB_data.%s" % ext)) # this will be the target of analysis

# Jinja2 renders templates to html (or other text formats)
# Hat tip to: https://bitbucket.org/Lawouach/cherrypy-recipes/src/c399b40a3251/web/templating/jinja2_templating?at=default
# Register the Jinja2 plugin
env = Environment(loader=FileSystemLoader(os.path.join(code_dir,"template")))
Jinja2TemplatePlugin(cherrypy.engine, env=env).subscribe()

# Register the Jinja2 tool
cherrypy.tools.template = Jinja2Tool()

# This function checks if the user is connected via https and if not redirects to it
# In case users are worried about privacy, we want to make sure uploads are not done 
# in the clear!
def force_https():
    secure = cherrypy.request.scheme == 'https'
    if not secure:
        url = urlparse.urlparse(cherrypy.url())
        secure_url = urlparse.urlunsplit(('https', url[1], url[2], url[3], url[4]))
        raise cherrypy.HTTPRedirect(secure_url)

# check for https on every request cherrypy handles
cherrypy.tools.force_https = cherrypy.Tool('before_handler', force_https)
#def interpolator(next_handler, *args, **kwargs):
#    filename = cherrypy.request.config.get("template")
#    cherrypy.response.template = env.get_template(filename)
#    response_dict = next_handler(*args, **kwargs)
#    return cherrypy.response.template.render(**response_dict)
#cherrypy.tools.jinja = HandlerWrapperTool(interpolator)

class Root: # the root serves (mostly) static site content

  # home/landing/welcome page
  @cherrypy.expose
  def index(self):
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    template = env.get_template("index.html")
    response_dict = {} #"foo":"hi", "bar":"there"}
    return template.render(**response_dict)

  # information about the Green Button data format and obtaining GB data
  @cherrypy.expose
  def gbdata(self):
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    template = env.get_template("gbdata.html")
    response_dict = {}
    return template.render(**response_dict)

  # information about this project
  @cherrypy.expose
  def about(self):
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    template = env.get_template("about.html")
    response_dict = {}
    return template.render(**response_dict)

  # information for developers interested int he project
  @cherrypy.expose
  def developers(self):
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    template = env.get_template("developers.html")
    response_dict = {}
    return template.render(**response_dict)
  
  # information for test users
  @cherrypy.expose
  def usertest(self):
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    template = env.get_template("usertest.html")
    response_dict = {}
    return template.render(**response_dict)

  # link to download static sample files - forces save dialog
  # this is important because otherwise some file types are 
  # served to the browser by default (i.e. *.xml)
  @cherrypy.expose
  def download(self,fileName):
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    baseDir = os.path.join(cherrypy.config.get("app.root"),'static','sample')
    # serve_download is a handy helper function from the server
    return serve_download(os.path.join(baseDir,fileName))

  # for testing dynamically generated content. This forces a re-genrate
  # of the pdf
  # TODO: turn off for production app?
  @cherrypy.expose
  def debugReport(self,workDir=None):
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    html = False
    if html:
      template = env.get_template("reportTemplate.html")
      response_dict = {"building" : cherrypy.session.get("building",None)}
      print response_dict["building"].score
      return template.render(**response_dict)
    else: 
      b = cherrypy.session.get("building",None)
      pm = PlotMaker(b,getUserDir(),cherrypy.config.get("wkhtmltopdf.bin"))
      pm.generateFiles()
      return self.dynamic("custom_report.pdf")

  # paths starting with 'dynamic' handle access to the files associated with the custom session
  @cherrypy.expose
  def dynamic(self,fileName,download=False):
    print 'request for dynamic file location:',fileName
    # guard against fishing trips for other files
    fileName = os.path.split(fileName)[1] # remove any extra path information - we just want the file name
    ext = "." + fileName.split(".")[-1]
    if not ext in (".png",".pdf",".jpg",".html",".csv"): raise Exception("Unrecognized file type requested.")
    
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    b = cherrypy.session.get("building",None)
    pm = PlotMaker(b,getUserDir(),cherrypy.config.get("wkhtmltopdf.bin"))
    pm.waitForImages() # generate images if necessary
    pme = pm.getError() # this is how we learn if there were errors in the image generation thread
    if pme is not None: raise Exception("File generation failed: " + pme) # the file generation failed, so we need to handle this error somehow
    baseDir = os.path.join(cherrypy.config.get("app.root"),getUserDir())
    print(os.path.join(baseDir,fileName))
    if download: return serve_download(os.path.join(baseDir,fileName))
    return serve_file(os.path.join(baseDir,fileName), content_type=mimetypes.types_map.get(ext,"text/plain"))

# this class handles the feedback functions of the tool.
# it serves the web form and writes the comments to a txt 
# file named with the session id
class FeedbackService(object):
  @cherrypy.expose
  def index(self):
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    template = env.get_template("feedback.html")
    response_dict = {}
    return template.render(**response_dict)
  
  @cherrypy.expose
  def doFeedback(self,**params):
    from random import randint
    fb = params.get('feedback','Huh? Missing feedback!')
    sId = 'unknown_%0.4i' % randint(1,1000)
    try: sId = cherrypy.session._id
    except: pass # Not sure how this could happen. go with the default.
    count = cherrypy.session.get("count", 0)
    fileName = os.path.join(cherrypy.config.get("work.file.dir"),'feedback_%s.txt' % sId)
    with open(fileName,'ab') as fbFile:
      fbFile.write('sId: %s (count %i)\n' % (sId,count))
      fbFile.write('Feedback:\n%s\n' % (fb))
    template = env.get_template("feedback_thx.html")
    response_dict = {}
    return template.render(**response_dict)

class UploadService(object): 
  """Class to handle file upload related tasks."""

  formOptions = {
    "hvac_type" : ("Electric heat / AC", "Electric heat / No AC",  
                  "Non-electric heat / AC", "Non-electric heat / No AC",
                  "Other", "Do not know"),
    "bldg_type" : ( "Stand alone house", "Attached/row house", "Manufactured/mobile home", "2-4 Unit building", # res
                    "5+ Unit building", "Apartment", "Condo", "Other residential",
                    "------------------",
                    "Office", "Laboratory", "Warehouse", "Food sales",                         # comm                                       
                    "Convenience store", "Grocery store/food market", "Public safety", 
                    "Health care", "Religious worship", "Public assembly", "Education", 
                    "Food service", "Nursing home", "Lodging", "Retail", "Service", 
                    "Parking garage", "Industrial", "Agricultural", "Data center", "Other commercial"),
  }

  @cherrypy.expose
  def index(self,**params):
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    template = env.get_template("upload.html")
    response_dict = {"formOptions" : self.formOptions,"errs" : {} }
    return template.render(**response_dict)

  # code to validate user inputs
  def validate(self,params):
    errs = {}
    if params.get("upFile").file is None: errs["upFile"] = "No file selected for upload"
    required = {  "bldg_name"    : "Missing building name",
                  "bldg_type"    : "Missing building type",
                  "bldg_vintage" : "Missing year of construction",
                  "hvac_type"    : "Missing heating and cooling information",
                  "occ_count"    : "Missing occupant count",
                  "bldg_zip"     : "Missing zip code",
                  "bldg_size"    : "Missing square footage"     }
    
    numeric = { "bldg_zip"      : "Zip must be 5 digits (only)",
                "bldg_vintage"  : "Vintage must be a valid year of 4 digits (only)",
                "bldg_size"     : "Square footage must be a positive number",
                "occ_count"     : "Occupancy must be a positive number" }

    
    for key in required:
      val = params.get(key,"").lower().strip()
      val = val.replace('-','') # remove dashes, which are used as separators
      val = val.replace('choose one','',)
      val = val.replace('select one','',)
      if val == "": errs[key] = required[key]
    
    for key in numeric:
      if params.get(key,"") == '?': continue # ? is an indicator that the user is unsure of the value
      try: 
        intVal = int( float( params.get(key,"foo") ) )
        if intVal < 0: raise ValueError("Must be positive")
      except ValueError as ve: errs[key] = numeric[key]

    if len(params.get("bldg_zip")) != 5: errs["bldg_zip"] = "Zip code must be 5 digits"
    
    if "bldg_zip" not in errs.keys():
      weather = WeatherData("weather") # dataDir
      zipLatLon = weather.zipMap().get(int(params["bldg_zip"]),None)
      if zipLatLon is None: errs["bldg_zip"] = "Invalid zip code not found in national list. Try another nearby?"
    return errs
    
  @cherrypy.expose
  def doUpload(self,**params):
    count = cherrypy.session.get("count", 0) + 1
    cherrypy.session["count"] = count
    sess = cherrypy.session
    sId = sess._id
    
    # run form submission validation
    errs = self.validate(params)
    if(len(errs) != 0):
      template = env.get_template("upload.html")
      return template.render( { "errs"        : errs, 
                                "params"      : params,
                                "formOptions" : self.formOptions } )
    
    # add the form data to the session so it is accessible until the end of the session
    sess.update(params)
    userDir = getUserDir()
    if not os.path.exists(userDir): os.makedirs(userDir) # create the directories as needed
    upFile = params.get("upFile",None)
    ext = upFile.filename.split(".")[-1]
    rawFilePath = os.path.join(userDir,"raw_GB_upload.%s" % (ext))
    size = 0
    with open(rawFilePath,"wb") as rawFile:
      while True:
        data = upFile.file.read(8192)
        if not data: break
        size += len(data)
        rawFile.write(data)
    print "uploaded file to: ", rawFilePath
    parseTargetFile = getDataFile(ext=ext)
    try:
      if(zipfile.is_zipfile(rawFilePath)): # try to find the electric interval data inside with a couple of heuristics
        # note that PGE's format looks like this "pge_electric_interval_data_2013-02-01_to_2013-04-28.xml"
        # note that SDGE's format looks like this "SDGE_Electric_15_Minute_06-30-2012_07-30-2013_20130801103424053.xml"
        matchPatterns = (".*electric.*\.xml",".*electric.*\.csv") # only PGE & SDGE for now. Could add more.
        zf = zipfile.ZipFile(rawFilePath,"r")
        matched = None
        for innerFile in zf.namelist():
          print(innerFile)
          for pattern in matchPatterns:
            match = re.match(pattern,innerFile,re.I) # note "re.match" searches from the beginning of the string, "re.search" scans. re.I makes the match case insensitive.
            if match is not None: 
              matched = match
              ext = match.string.split(".")[-1]
              parseTargetFile = getDataFile(ext=ext)
              with open(parseTargetFile,"wb") as target:
                source = zf.open(innerFile,"r")
                try: shutil.copyfileobj(source, target)
                finally: source.close()
              break
        if matched is None: raise Exception("""Unrecognized zip format. There are many providers of Smart Meter data,
                                   and they use whatever file naming convention they want so we don't always
                                   know what file to look for inside the zip file. Please report this via
                                   the feedback page, and try again with your xml file unzipped.""")
      else: # if(ext == "xml"):
        try: 
          os.remove(parseTargetFile) # in case a file was already uploaded during this session, get it out of the way
                                     # rename won"t overwrite an existing file on Windows... see os.rename
        except: pass # if it doesn"t exist, no problem. If it is currently in use, then next line will fail.
        os.rename(rawFilePath,parseTargetFile)
      #else: raise Exception("Unrecognized file type %s" % ext)

      parsedData = analysis.parseDataFile(parseTargetFile)

      dates = parsedData.getReadings()[0]
      dateDiff = [j-i for i, j in zip(dates[:-1], dates[1:])]
      #print dateDiff
      if(min(dateDiff) > datetime.timedelta(hours=1)): raise Exception("Time difference must be at most 1 hour between readings")
    
    #except xml.parsers.expat.ExpatError as ee:
    #  print ee
    #  errs["upFile"] = "Your file does not appear to be in a Green Button XML format. Please check the file and try again."
    except Exception as e: 
      print e
      errs["upFile"] = str(e)

    if(len(errs) != 0):
      template = env.get_template("upload.html")
      return template.render( { "errs"        : errs, 
                                "params"      : params,
                                "formOptions" : self.formOptions } )

    params["filename"]    = upFile.filename
    params["filesize"]    = size
    params["filetype"]    = str(upFile.content_type)
    params["upload_time"] = datetime.datetime.now()
    del params["upFile"] # remove the upload file obj
    # write params to working dir so session can be recreased
    with open(os.path.join(userDir,'params.pkl'),'wb') as paramFile:
      pickle.dump(params,paramFile)
    #with open(os.path.join(userDir,'params.pkl'),'rb') as paramFile:
    #  print pickle.load(paramFile)
    bldg = Building(parsedData.getReadings(),params['bldg_zip'],params)
    cherrypy.session["building"] = bldg
    weather = WeatherData("weather")
    first = bldg.days[0]
    last = bldg.days[-1]
    # warning. This can trigger a long download that is not in a separate thread
    bestWBAN = weather.closestWBAN(int(params["bldg_zip"]),first.year,first.month)
    sess["bestWBAN"]      = bestWBAN
    bldg.attr["bestWBAN"] = bestWBAN

    pm = PlotMaker(bldg,getUserDir(),cherrypy.config.get("wkhtmltopdf.bin"),sId)
    threading.Thread(target=pm.generateFiles).start()
    time.sleep(0.25) # let the lock file get written
    sess["filename"] = upFile.filename
    sess["filesize"] = size
    sess["filetype"] = upFile.content_type
    sess["filepath"] = parseTargetFile
    template = env.get_template("dataExplore.html")
    response_dict = {}
    response_dict.update(sess)
    return self.explore()
    #return "%s (%s): %s" % (upFile.filename,size,upFile.content_type)

  @cherrypy.expose
  def explore(self,**params):
    sess = cherrypy.session
    template = env.get_template("dataExplore.html")
    response_dict = {}
    response_dict.update(sess)
    return template.render(**response_dict)

# Deprecated. But it can dump the data associated with the current session. Sometimes useful.
class ImageService(object):
  def index(self,**params):
    b = cherrypy.session["building"]
    [dates,watts] = b.data
    rows = ["%s,%i" % (dates[i].strftime("%Y-%m-%d %H:%M"),watts[i]) for i in range(len(watts))]
    return '%s"s %s (%i kWh total):<br>' % (params.get("user","uploaded data"),params.get("fuel","electricity"),sum(watts)/1000) + "<br>".join(rows)
  index.exposed = True

if __name__ == "__main__":

  bft_conf = os.path.join(os.path.dirname(__file__), "fingerprint.conf")
  root          = Root()
  root.img      = ImageService()
  root.upload   = UploadService()
  root.feedback = FeedbackService()
  cherrypy.quickstart(root,config=bft_conf)

  # Disable the encode tool because it
  # transforms our dictionary into a list which
  # won"t be consumed by the jinja2 tool
  #cherrypy.quickstart(Root(), "", {"/": {"tools.template.on": True,
  #                                       "tools.template.template": "index.html",
  #                                       "tools.encode.on": False}})
