# Note: Because this parser will be parsing untrusted (user uploaded) xml,
# the package defusedxml isbeing used. It wraps vulnerable libraries
# in protective code that prevents several types of DOS and local file access
# attacks. See:
#http://docs.python.org/2/library/xml.html#xml-vulnerabilities
#https://pypi.python.org/pypi/defusedxml/
#from xml.etree import ElementTree # NOPE!
from defusedxml import ElementTree # import security patched xml.etree.ElementTree

import datetime as dt
from pytz import timezone
import re

ESPI_NS = 'http://naesb.org/espi'
ATOM_NS = "http://www.w3.org/2005/Atom"

def getInstance(gbXMLFile):
  '''For compatability with code that doesn't know what parser it is getting'''
  return GBData(gbXMLFile)

class GBData:
  '''This program parses the Green Button XML format of interval meter data
     While the format is carefully structured with different sections
     and careful namespace use in each'''
  def __init__(self,gbXMLFile):
    self.tree = ElementTree.parse(gbXMLFile)
    self.root = self.tree.getroot()
    self.parsed = self.dataStructure()

  # the structure of a feed is to have a single feed
  # with N usage points, with M ReadingBlocks
  # typical usage is 1 x 1, but there are others
  #
  #Feed
  #  N x 
  #  UsagePoint
  #  LocalTimeParameters
  #    M x
  #    MeterReading
  #    ReadingType
  #    IntervalBlock
  #    ElectricPowerUsageSummary
  def dataStructure(self):
    out = {
      'feedType' : None,
      'UsagePoints' : []
    } 
    out['feedType']  = self.text(self.root,'./{%s}title'     % (ATOM_NS))
    out['updated']   = self.text(self.root,'./{%s}updated'   % (ATOM_NS))
    out['published'] = self.text(self.root,'./{%s}published' % (ATOM_NS))
    currUsagePoint = None
    currReadingBlock = {}
    for entry in self.getEntries(self.root):
      (entryType,instance) = self.entryType(entry)
      if entryType == 'UsagePoint':
        siteName = None
        siteNameX = entry.find('{%s}title' % (ATOM_NS))
        if siteNameX is not None: siteName = siteNameX.text
        if currUsagePoint is not None: 
          if len(currReadingBlock) > 0:
            currUsagePoint['ReadingBlock'].append(currReadingBlock)
            currReadingBlock = {}
          out['UsagePoints'].append(currUsagePoint)
        currUsagePoint = {
          'name'         : '%s  [%s]' % (siteName,instance),
          'tzOffset'     : 0,
          'ReadingBlock' : [],
        }
      elif entryType == 'LocalTimeParameters':
        offset = entry.find('.//{%s}tzOffset' % (ESPI_NS))
        if offset is not None:
          currUsagePoint['tzOffset'] = int(offset.text)
      elif entryType == 'MeterReading':
        if currReadingBlock.get('readings') is not None: 
          currUsagePoint['ReadingBlock'].append(currReadingBlock)
          currReadingBlock = {}
        currReadingBlock = {
          'instance'  : instance,
          'updated'   : self.text(entry,'./{%s}updated'   % (ATOM_NS)),
          'published' : self.text(entry,'./{%s}published' % (ATOM_NS)),
        }
      # provides 
      #  <accumulationBehaviour>4</accumulationBehaviour>
      #  <commodity>1</commodity>
      #  <dataQualifier>0</dataQualifier>
      #  <flowDirection>1</flowDirection>
      #  <kind>0</kind>
      #  <phase>0</phase>
      #  <powerOfTenMultiplier>0</powerOfTenMultiplier>
      #  <timeAttribute>7</timeAttribute>
      #  <uom>72</uom>
      #  <currency>
      elif entryType == 'ReadingType':
        currReadingBlock['readingInstance'] = instance
      elif entryType == 'IntervalBlock':
        readingsX = entry.findall('.//{%s}IntervalReading' % ESPI_NS)
        # there can be multiple IntervalBlocks that organizes readings in arbitrary groups
        # here we just want to append the newer readings to the existing readings so we get
        # them all eventually
        readings = currReadingBlock.get('readings',[(),()]) # default is two len zero tuples
        newReadings = self.parseReadings(readingsX,currUsagePoint['tzOffset'])
        readings = [readings[0] + newReadings[0], readings[1] + newReadings[1]] # append new ones
        currReadingBlock['readingCount'] = len(readings[0])
        currReadingBlock['readings'] = readings
      else: print 'ignoring entry: %s %s' % (entryType,instance)
    if currUsagePoint is not None: 
      if len(currReadingBlock) > 0: 
        currUsagePoint['ReadingBlock'].append(currReadingBlock)
      out['UsagePoints'].append(currUsagePoint)
    return out

  def text(self,node,path):
    targetNode = node.find(path)
    if targetNode is not None: return targetNode.text
    else:                      return None

  def isType(self,node,typeName):
    if typeName is None: return False
    return self.entryType(node)[0] == typeName

  def getEntries(self,node,entryName=None):
    entries = []
    for entry in node.findall('./{%s}entry' % ATOM_NS):
      if entryName is None: entries.append(entry) # no type? return them all
      else: 
        if self.isType(entry,entryName): entries.append(entry)
    return entries

  def entryType(self,node):
    for linkX in node.findall('./{%s}link' % ATOM_NS):
      if linkX.get('rel',None) == 'self':
        link = linkX.get('href',None)
        parts = link.split('/') 
        if len(parts) >= 2:
          return (parts[-2],parts[-1]) # last two pieces of the path are type name and number
        elif len(parts) == 1:
          return (link,'001')
    for content in node.findall('./{%s}content' % ATOM_NS):
      contentType = content[0].tag.split('}')[-1] # the namespace in {} is part of the tag name, so we split it away
                                                  # when the } isn't there, the name is still returned properly
      return (contentType,'001') # there should be only one, but if there are multiple, this returns the first
    return (None,None)

  def parseReadings(self,readingsX,offset=0):
    tree = self.tree
    readings = []
    #tz = timezone('US/Pacific') 
    # Look for all elements that contain readings. They will be in the form:
    #    <IntervalReading>
    #        <!-- interval row numnber: 2 -->
    #        <!-- start date: 1/1/2011 -->
    #        <!-- raw value: 0.703860721 -->
    #        <cost>3454000</cost> <!-- texas only -->
    #        <timePeriod>
    #            <duration>3600</duration>
    #            <start>1293840000</start>
    #        </timePeriod>
    #        <value>703</value>
    #    </IntervalReading>
    for readingX in readingsX:
      dStr = readingX.find('.//{%s}timePeriod/{%s}start' % (ESPI_NS,ESPI_NS)).text
      vStr = readingX.find('./{%s}value' % ESPI_NS).text
      # UNIX time is GMT, we currently assume Green Button data is provided in local time
      date  = dt.datetime.fromtimestamp(int(dStr)-offset)# convert unix time to a date objct
      #date.replace(tzinfo=tz)
      watts = int(vStr)
      readings.append((date,watts))
    return zip(*readings)

  def getReadings(self,usagePointIdx=0,intervalBlockIdx=0):
    try: return self.parsed['UsagePoints'][usagePointIdx]['ReadingBlock'][intervalBlockIdx]['readings']
    except IndexError as ie: return None
    

  def writeReadings(self,readings,out=None):
    rows = ['%s,%i' % (reading[0].strftime('%Y-%m-%d %H:%M'),reading[1]) for reading in readings]
    if out is None:
      print rows
    else:
      print "writing to %s" % (out)
      with open(out,'w') as f:
        for row in rows:
          f.write(row + '\n')

  def summarize(self):
    title = self.root.find('./{%s}title' % (ATOM_NS))
    if title is not None: print 'Feed: %s' % title.text
    sites = self.getEntries(self.root,'UsagePoint')
    print '  %d UsagePoints' % len(sites)
    for i,siteInfo in enumerate(sites):
      siteName = siteInfo.find('{%s}title' % (ATOM_NS))
      if siteName is not None: print '  site [%d]: %s' % (i,siteName.text)
    
    blocks = self.getEntries(self.root,'IntervalBlock')
    print '  %d sets of readings' % len(blocks)
    for i,block in enumerate(blocks):
      readings = block.findall('.//{%s}IntervalReading' % ESPI_NS)
      print('    %d IntervalReadings' % len(readings))

if __name__ == '__main__':
  import glob
  dataSources = glob.glob('example/GBdata/*.xml')
  #dataSources = glob.glob('example/GBdata/SDGE_Electric_60_Minute.xml')
  
  for source in dataSources:
    print source
    gbd = GBData(source)
    #gbd.summarize()
    #gbd.writeReadings(gbd.parseGBData(source)['readings'],'csv/'+source+'.csv')
    #import json
    #print json.dumps(gbd.parsed,default=lambda x: None,indent=2)
    for usagePoint in gbd.parsed['UsagePoints']:
      print usagePoint['name']
      for block in usagePoint['ReadingBlock']:
        [dates,rates] = block['readings'] # [dates,rates]
        print 'interval block [%s] %d obs' % (block['instance'], len(dates))
    [dates,rates] = gbd.getReadings()
    print dates[0],rates[0]
    