#!/usr/bin/python

'''Data class that loads, parses, and visualizes GB XML data'''
import os
import cherrypy
import datetime
import re
import time # for time.sleep

import numpy as np
import scipy.stats

import matplotlib
matplotlib.use('Agg') # use the headless Agg graphics environment

from matplotlib import mlab # set of matlab compatible functions, like ma for moving averages
from matplotlib import dates  as mpld
from matplotlib import ticker as mplt
from matplotlib import cm # colormap

#import matplotlib.pyplot as plt     # BAD!!! pyplot is not thread safe !!
from matplotlib.figure import Figure # use matplotlib.figure.Figure and OO API only
from matplotlib.backends.backend_agg import FigureCanvasAgg as FigureCanvasPng # for rendering figures to png images
from matplotlib.backends.backend_pdf import FigureCanvasPdf # for rendering figures to pdfs

from StringIO import StringIO # library that allows interaction with Strings as though they are files

from jinja2        import Environment, FileSystemLoader # jinja2 template rendering adapters for CherryPy
from WeatherData   import WeatherData # Custom class that manages weather data
import GBParse                        # Custom class that parses the GreenButtonXML data format
import CSVParse                       # Custom class that does simple csv parsing

# Enable the Jinja2 engine
current_dir = os.path.dirname(os.path.abspath(__file__)) # the dir this file is in
jinjaEnv = Environment(loader=FileSystemLoader(os.path.join(current_dir,'template')))

# map parsers to extensions
parserMap = {
  'xml':GBParse,
  'csv':CSVParse,
}
# load and parse data from a source file
dataCache = {} # init a quick memory cache
def parseDataFile(source,useCache=False):
  # the source is the file containing the data (i.e. GB.xml or GB.csv)
  # a quick dict based memory cache can be used for repeated access
  readings = None
  if useCache: parsedData = dataCache.get(source,None)
  if readings is None: 
    #fileLock.acquire()
    try:
      ext = source.split(".")[-1]
      dataParser = parserMap.get(ext,None) # find the extension appropriate parser
      # TODO: more aggressive failure for unknown extensions?
      if dataParser is None: 
        print 'Warning: no parser matching extension %s. Using GBParse' % (ext)
        dataParser = GBParse
      parsedData = dataParser.getInstance(source)
      dataCache[source] = parsedData
    finally: pass #fileLock.release()
  return parsedData

class Building(object):
  def __init__(self,intervalData,zip5,attr):
    self.data = intervalData  # tuple of datetime, watt lists
    self.attr = attr          # dict of named building attributes
    self.occupancy = float(self.attr.get('occ_count',1))
    self.sqft      = float(self.attr.get('bldg_size',1))

    self.zip5 = zip5
    n = len(self.data[0])-1
    DOW = [x.weekday() for x in self.data[0]]   # days of the week mon=0 sun=6
    DOM = [x.day       for x in self.data[0]]   # days of the month
    MOY = [x.month     for x in self.data[0]]   # month of year
    #print np.where(np.diff(DOM))[0] # for some reason, where returns a tuple with the array inside
    self.dayBreaks  = np.concatenate( (np.where(np.diff(DOM))[0],[n]))   # indices where DOMs change
    self.weekBreaks = np.concatenate( (np.where(np.diff(DOW)<0)[0],[n])) # indices where DOWs wrap back to mon
    dayLengths      = np.diff(self.dayBreaks)   # distance between dayBreaks in nObs
    counts          = np.bincount(dayLengths)   # counts of each value
    self.obsPerDay  = np.argmax(counts)         # maximum count is the modal nObs/day
    self.obsPerWeek = self.obsPerDay * 7        # 7 * nObs/day = nObs/week
    
    self.dailyData = self.reshape(self.data,wrap='day')
    self.weeklyData = self.reshape(self.data,wrap='week')

    wattsD = self.dailyData[1]
    datesD = self.dailyData[0]

    wattsW = self.weeklyData[1]
    datesW = self.weeklyData[0]
    
    # compute daily aggretage values
    self.days        = [x.date() for x in datesD[:,0]] # convert from datetime to date objects
    self.dailyStats  = self.gridStats(wattsD,axis=1)   # one number per day
    self.dayStats    = self.gridStats(wattsD,axis=0)   # one number per time of day
    self.weeklyStats = self.gridStats(wattsW,axis=1)   # one number per week
    self.weekStats   = self.gridStats(wattsW,axis=0)   # one number per time of week
    self.stats = {
      'mean'  : np.mean(self.dailyStats['mean']), # mean
      'max'   : np.mean(self.dailyStats['max']),  # max
      'min'   : np.mean(self.dailyStats['min']),  # min
      'mxmn'  : np.mean( np.ma.masked_invalid(self.dailyStats['mxmn']) ), # max/min
      'range' : np.mean(self.dailyStats['max'] - self.dailyStats['min']), # range
    }
# todo: support OLS regression and other forms of analysis
# will require more robust data cleansing and time diffs
#
#    import ols
#    wd = WeatherData('weather')
#    
#    y = np.matrix(self.dailyStats['mean'] * 24)
#    (dates,tout) = wd.matchWeather(self.days,self.zip5)
#    x = np.matrix(tout)
#    print len(x)
#    print len(y)
#    print len(self.days)
#    print y
#    lm = ols.ols(y,x,'y',['tout'])
#    print(dir(lm))
#    print(lm.summary())
    # performance score is intended to be a quantified metrics of home energy 
    # performance supported by benchmarking data.
    self.score = self.performanceScores()


  def gridStats(self,dataGridRaw,axis=0):
    '''This function calculates some standard statistics for the data passed in.
       It assumes a 2D grid of data readings with 1 row per day or week and 
       columns across all times of day/week. i.e. the output of reshape()...'''
    dataGrid = np.ma.masked_array(dataGridRaw,np.isnan(dataGridRaw))
    #dataGrid = dataGridRaw
    out = {
      'mean'  : dataGrid.mean(axis=axis),
      'std'   : dataGrid.std(axis=axis),
      #'max'   : np.max(dataGrid,axis=axis), # true max - too volatile
      #'min'   : np.min(dataGrid,axis=axis), # true min - too ephemeral
    }
    # HACK!
    # percentile doesn't respect masked nans, so we need to fill with a respectable value
    # and hope that it doesn't throw things off too much. For the record, nan is treated as 
    # a very large number, so max in particular is impacted
    dataGrid = dataGrid.filled(dataGrid.mean())
    out['max'] = np.percentile(dataGrid,95,axis=axis)
    out['min'] = np.percentile(dataGrid,5,axis=axis)
    out['mxmn'] = np.ma.masked_invalid( np.divide(out['max'],out['min']) )
    return(out)

  def reshape(self,data=None,wrap='day'):
    if(data == None): data = self.data # use the stored data if none is passed in
    dates = np.array(data[0])
    watts = np.array(data[1])
    if(wrap == 'day'): 
      wrapAt  = self.obsPerDay
      breaks  = self.dayBreaks
    if(wrap == 'week'): 
      wrapAt  = self.obsPerDay * 7
      breaks  = self.weekBreaks
    
    foldedWatts = self.breakAt(watts,breaks,wrapAt)
    msk = ~(np.isfinite(foldedWatts)) # build a mask over the non-finite readings
    # apply the mask to the watts readings and dates
    #print msk
    wattsA = np.ma.masked_where(msk,foldedWatts)
    datesA = np.ma.masked_where(msk,self.breakAt(dates,breaks,wrapAt))
    return (datesA,wattsA)

  # this breaks the array of values at the indices specified in the array of breaks
  # into a a matrix with nCols cols and len(breaks)-1 rows
  # note that it starts its first row at the first break index, so if the first break is not
  # 0, it will drop the values leading up to the first break. This is the desired behavior
  # when using data starting with a partial day or week 
  # WARNING: this method will produce potentially  inaccurate or error prone results if the 
  # number of observations per day/week changes over time
  def breakAt(self,vals,breaks,nCols):
    n = len(breaks) # number of rows
    m = nCols
    data = None
    if type(vals[0]) == datetime.datetime: 
      data = np.empty((n-1,m),dtype=np.object)
      data.fill(None) # blank for objects should be None...
    else:
      data = np.empty((n-1,m),dtype=float)
      data.fill(np.nan) # blank for floats should be nans...
    for i in range(n-1):
      begin = breaks[i] + 1
      end = min(begin+m,breaks[i+1]+1) # if the row is too long, truncate it at the agreed row length
      data[i,0:(end-begin)] = vals[begin:end]
      #print vals[begin:end]
    return data
  
  def performanceScores(self):
    '''Method designed to score the performance of a building via benchmarking... 
       when the data becomes available. This can be ignored for the time being. '''
    stats = np.array([
      np.mean(self.dailyStats['mean']), # mean
      np.mean(self.dailyStats['max']),  # max
      np.mean(self.dailyStats['min']),  # min
      np.mean(np.ma.masked_invalid(self.dailyStats['mxmn']) ), # max/min
      np.mean(self.dailyStats['max'] - self.dailyStats['min']), # range
      0.5                               # duration
    ])                             

    weights = np.array([
      [0.3,0,0,0,1.0],
      [0.3,-0.2,0.6,-0.8,0.5],
      [0.3,0.7,0.1,0.5,0],
      [0.3,0.7,0.7,0.5,0],
      [0.3,0.8,0.3,0.5,0],
      [0.3,0.2,1,-0.5,0] ])
    
    out = {
      'shutoff_duration' : 0.0,
      'shutoff_depth' : 1.0,
      'temperature_sensitivity' : 0.6,
      'equipment_ee' : 0.8,
      'usage_intensity' : 0.9,
      'vampire_loads' : 0.3,
    }
    return out

class PlotMaker(object):
  
  def __init__(self,building,workDir,wkhtmltopdf,sessionId='unknown session'):
    self.building    = building
    self.workDir     = workDir
    self.wkhtmltopdf = wkhtmltopdf
    self.sessionId   = sessionId

    # Use these for a poor man's transactional generation of files for
    # thread safety. 
    # Code that relies on the images can block when the imageLock file
    # is present, and return an error when the error file is present
    self.lockFile  = os.path.join(self.workDir,'_IMG_LOCK')
    self.errorFile = os.path.join(self.workDir,'_ERROR')

  def waitForImages(self):
    while os.path.isfile(self.lockFile):
      #print 'tic'
      time.sleep(0.5)

  def getError(self):
    if not os.path.isfile(self.errorFile): return None
    msg = None
    with open(self.errorFile,'r') as err: msg = err.read()
    return msg
  
  def plot(self):
    [dates,watts] = self.building.data
    fig = Figure(facecolor='white',edgecolor='none')
    ax  = fig.add_subplot(111)
    ax.plot(dates,[w/1000.0 for w in watts])
    monthFmt = mpld.DateFormatter('%m/%d/%y')
    months   = mpld.MonthLocator()  # every month
    ax.xaxis.set_major_locator(months)
    ax.xaxis.set_major_formatter(monthFmt)
    fig.autofmt_xdate()
    ax.set_title('Plot of %s data for %s' % ('electricity','uploaded data'))
    ax.set_xlabel('Date')
    ax.set_ylabel('kW')
    ax.grid(True)
    return fig

  def duration(self):
    '''Load duration curve'''
    [dates,watts] = self.building.data
    fig = Figure(facecolor='white',edgecolor='none')
    ax = fig.add_subplot(111)
    ax.plot(sorted([w/1000.0 for w in watts]))
    ax.set_title('Load duration of %s data for %s' % ('electricity','uploaded data'))
    ax.set_ylabel('kW')
    ax.set_xlabel('ranked hour of the year')
    ax.grid(True)
    return fig

  def dailyMaxMin(self):
    [datesA,wattsA] = self.building.dailyData
    fig = Figure(facecolor='white',edgecolor='none')
    ax = fig.add_subplot(111)
    dailyMax  = self.building.dailyStats['max']  / 1000
    dailyMin  = self.building.dailyStats['min']  / 1000
    dailyMean = self.building.dailyStats['mean'] / 1000
    dts = self.building.days
    ax.fill_between(dts, dailyMin, dailyMax, facecolor='#e6e6e6', edgecolor='#e6e6e6')
    ax.plot(dts,dailyMax,color='#aa2222',alpha=0.2,label='Daily max')
    ax.plot(dts,dailyMean,color='#000000',label='Daily mean')
    ax.plot(dts,dailyMin,color='#2222aa',alpha=0.2,label='Daily minimum')
    monthFmt = mpld.DateFormatter('%m/%d/%y')
    ax.set_title('Daily min, mean, and max (kW)')
    ax.xaxis.set_major_formatter(monthFmt)
    fig.autofmt_xdate()
    ax.set_ylabel('kW')
    ax.set_xlabel('Date')
    ax.grid(True)
    # rotate lables
    #for l in ax.xaxis.get_majorticklabels(): l.set_rotation(70)
    ax.legend(loc='upper right')
    ax.yaxis.set_major_formatter(mplt.FormatStrFormatter('%0.1f'))
    return fig

  def dailyToutKWh(self):
    [datesA,wattsA] = self.building.dailyData
    fig = Figure(facecolor='white',edgecolor='none')
    ax = fig.add_subplot(111)
    # multiple the mean by 24 hrs to get kWh - this is independent of observation interval
    daySum  = self.building.dailyStats['mean']*24/1000
    wd = WeatherData('weather')
    (dates,tout) = wd.matchWeather(self.building.days,self.building.zip5)
    #ax.plot(dts,daySum,'o',color='#000000',alpha=1,label='Daily kWh')
    #ax.set_xlabel('Date')
    ax.set_xlabel('Mean daily temperature (F)')
    ax.plot(tout,daySum,'o',color='#9970AB',alpha=1,label='Weekday kWh')
    # weekend dates
    wknd = np.where([int(dt.isoweekday() > 5) for dt in dates])[0].tolist()
    print wknd
    ax.plot(np.array(tout)[wknd],np.array(daySum)[wknd],'o',color='#5AAE61',alpha=1,label='Weekend kWh')
    # todo: identify weekends and color differently
    ax.set_title('Daily energy (kWh)')
    ax.set_ylabel('kWh')
    ax.grid(True)
    ax.legend(loc='upper right')
    ax.yaxis.set_major_formatter(mplt.FormatStrFormatter('%0.1f'))
    return fig

  # todo: calculate separate values for summer and winter
  def meanWeek(self):
    [datesA,wattsA] = self.building.weeklyData
  
    fig = Figure(facecolor='white',edgecolor='none')
    ax = fig.add_subplot(111)
    nObs = datesA.shape[1]
    # get a set of dates that can be used for lableing the date axis
    # it doesn't matter which ones, but they need to correctly span the weekdays
    # and have no blanks - and the real data can have blanks...
    dt0 = datesA[0,0]
    dt = datetime.timedelta(days=7.0 / nObs)
    dts = [dt0 + dt * x for x in range(nObs)]
    ax.plot(dts,self.building.weekStats['mean']/1000,'-',color='#000000',alpha=1,label='Average kW')
    ylm =  ax.get_ylim()
    #ax.plot(dts,self.building.weekStats['mean'] + self.building.weekStats['std'],'--' ,color='#000000',alpha=0.5,label='+ 1 std')
    #ax.plot(dts,self.building.weekStats['mean'] - self.building.weekStats['std'],'--',color='#000000',alpha=0.5,label='- 1 std')
    ax.fill_between(dts,[0] * len(self.building.weekStats['mean']),self.building.weekStats['mean']/1000, facecolor='#e6e6e6', edgecolor='#e6e6e6' )
    ax.plot(dts,self.building.weekStats['max']/1000,'-',color='#ff0000',alpha=0.2,label='Max kW')
    ax.plot(dts,self.building.weekStats['min']/1000,'-',color='#0000ff',alpha=0.2,label='Min kW')
    ax.xaxis.set_major_locator(mpld.DayLocator())
    ax.xaxis.set_major_formatter(mpld.DateFormatter('%a')) # just the day of week
    for label in ax.xaxis.get_majorticklabels(): # move the labels into the day range
      label.set_horizontalalignment('left')
    # force 0 as minimum value and make room at the top for the legend
    ax.set_ylim(bottom=0,top=ylm[1]*1.2)
    ax.set_title('Average weekly profile')
    ax.set_ylabel('kW')
    ax.set_xlabel('Day of week')
    ax.grid(True)
    ax.legend(loc='upper right')
    ax.yaxis.set_major_formatter(mplt.FormatStrFormatter('%0.1f'))
    return fig

  def histogram(self):
    [dates,watts] = self.building.data
    fig = Figure(facecolor='white',edgecolor='none')
    ax = fig.add_subplot(111)
    ax.hist([w/1000.0 for w in watts], 200, normed=1, facecolor='green', alpha=0.75)
    #ax.plot(dates,[w/1000.0 for w in watts])
    #monthFmt = d.DateFormatter('%m/%d/%y')
    #months   = d.MonthLocator()  # every month
    #ax.xaxis.set_major_locator(months)
    #ax.xaxis.set_major_formatter(monthFmt)
    #fig.autofmt_xdate()
    ax.set_title('Histogram of %s data for %s' % ('electricity','uploaded data'))
    ax.set_xlabel('kW')
    ax.set_ylabel('count')
    ax.grid(True)
    return fig

  def heatmap(self):
    [dates,watts] = self.building.data
    clipMax = scipy.stats.scoreatpercentile(watts,per=95)/1000
    clipMin = scipy.stats.scoreatpercentile(watts,per=0)/1000
    #watts[watts > clipMax] = clipMax

    [datesA,wattsA] = self.building.dailyData

    (m,n) = wattsA.shape
    fig = Figure(figsize=(10,6),facecolor='white',edgecolor='none')
    ax = fig.add_subplot(111)
    
    #print 'shapes:',m,n
    #print cm.coolwarm
    p = ax.imshow(wattsA/1000, interpolation='nearest', aspect='auto', cmap=cm.coolwarm, extent=[0,n*20*2,0,m*2])
    p.cmap.set_over('grey')
    cbar = fig.colorbar(p,ax=ax,shrink=0.8)
    cbar.set_label('kW')
    p.set_clim(clipMin, clipMax)
    ax.set_xticks(range(0,n*40+1,n*40/24*2))
    ax.set_xticklabels(['%iam' % x for x in [12]+range(2,12,2)] + ['%ipm' % x for x in [12] + range(2,12,2)] + ['12am'])
    # rotate lables
    for l in ax.xaxis.get_majorticklabels(): l.set_rotation(70)
    ax.set_yticks(range(1,m*2+1,30*2))
    ax.format_ydata = mpld.DateFormatter('%m/%d')
    ax.set_yticklabels([x.strftime('%m/%d/%y') for x in datesA[-1:1:-30,0]])
    #fig.autofmt_ydate()
    ax.tick_params(axis='both', which='major', labelsize=8)
    ax.set_title('Heat map of %s data for %s' % ('electricity','uploaded data'))
    ax.set_xlabel('Hour of day')
    ax.set_ylabel('Date')
    ax.grid(True)
    fig.subplots_adjust(top=1.0, left=0.20)
    return fig

  def loadShape(self):
    [dates,watts] = self.building.data
    #clipMax = scipy.stats.scoreatpercentile(watts,per=95)
    #clipMin = scipy.stats.scoreatpercentile(watts,per=0)
    #watts[watts > clipMax] = clipMax

    [datesA,wattsA] = self.building.dailyData
    nObs = datesA.shape[1]
    dt0 = datesA[0,0]
    dt = datetime.timedelta(days=1.0 / nObs)
    dts = [dt0 + dt * x for x in range(nObs)]
    wattsA = np.ma.masked_array(wattsA,np.isnan(wattsA)) # mask nans 
    dayMeans = self.building.dailyStats['mean']
    maxVal = max(dayMeans)
    minVal = min(dayMeans)
    maxIdx = dayMeans.argmax()
    minIdx = dayMeans.argmin()
    DOW = np.array([x.date().weekday() for x in datesA[:,0]]) # 0 = Mon, 6 = Sun
    WKND = DOW >  4
    WKDY = DOW <= 4
    meanLoad = wattsA.mean(axis=0)
    wkdnLoad = wattsA[WKND,].mean(axis=0)
    wkdyLoad = wattsA[WKDY,].mean(axis=0)
    (m,n) = wattsA.shape
    fig = Figure(figsize=(8,4l),facecolor='white',edgecolor='none')
    ax = fig.add_subplot(121) 
    
    meanDay = self.building.dayStats['mean']/1000
    ax.plot(dts,meanLoad/1000,'-',color='#000000',alpha=1,label='average day: %0.1f kWh' % (sum(meanLoad/1000)))
    
    #ax.plot(datesA[0,:],self.building.weekStats['mean'] + self.building.weekStats['std'],'--' ,color='#000000',alpha=0.5,label='+ 1 std')
    #ax.plot(datesA[0,:],self.building.weekStats['mean'] - self.building.weekStats['std'],'--',color='#000000',alpha=0.5,label='- 1 std')
    ax.fill_between(dts,[0] * len(meanLoad),meanLoad/1000, alpha=1,facecolor='#F0F0F0', edgecolor='#F0F0F0' )
    ax.plot(dts,wkdnLoad/1000,'s-',color='#5AAE61',alpha=0.5,markersize=3,label='average weekend: %0.1f kWh' % (sum(wkdnLoad/1000)))
    ax.plot(dts,wkdyLoad/1000,'o-',color='#9970AB',alpha=0.5,markersize=3,label='average weekday: %0.1f kWh' % (sum(wkdyLoad/1000)))

    ax.xaxis.set_major_locator(mpld.HourLocator(byhour=[0,4,8,12,16,20,24]))
    ax.xaxis.set_major_formatter(mpld.DateFormatter('%H')) # just the day of week
    for label in ax.xaxis.get_majorticklabels(): # move the labels into the day range
      label.set_horizontalalignment('left')
    # force 0 as minimum value and make room at the top for the legend
    ylm =  ax.get_ylim()
    #ax.plot(dts,wattsA[-1,:]/1000,'-',color='#525252',alpha=0.4,markersize=2,label='last day: %0.1f kWh (%s)' % (np.sum(wattsA[-1,:]/1000),datesA[-1,0].date()))
    ax.set_ylim(bottom=0,top=ylm[1]*1.3)
    ax.set_title('Average days')
    ax.set_ylabel('kW')
    ax.set_xlabel('Hour of day')
    ax.grid(True,alpha=0.2)
    ax.legend(loc='upper left',prop={'size':8},markerscale=1)
    ax.yaxis.set_major_formatter(mplt.FormatStrFormatter('%0.1f'))
    
    
    ax2 = fig.add_subplot(122) 

    ax2.plot(dts,meanLoad/1000,'-',color='#000000',alpha=1,label='Average day: %0.1f kWh' % (sum(meanLoad/1000)))
    ax2.plot(dts,wattsA[maxIdx,:]/1000,'-',color='#F03B20',alpha=0.7,label='Max day %0.1f kWh (%s)' % (np.sum(wattsA[maxIdx,:]/1000),datesA[maxIdx,0].date()))
    ax2.fill_between(dts,[0] * len(wattsA[maxIdx,:]),wattsA[maxIdx,:]/1000, facecolor='#F03B20', edgecolor='#F03B20',alpha=0.1 )
    ax2.fill_between(dts,[0] * len(meanLoad),meanLoad/1000,alpha=1, facecolor='#F0F0F0', edgecolor='#F0F0F0' )
    #ax2.plot(dts,wattsA[-1,:]/1000,'-',color='#000000',alpha=1,label='last day: %0.1f kWh (%s)' % (np.sum(wattsA[-1,:]/1000),datesA[-1,0].date()))
    #ax2.fill_between(dts,[0] * len(wattsA[-1,:]),wattsA[-1,:]/1000, facecolor='#e6e6e6', edgecolor='#e6e6e6' )
    ax2.plot(dts,wattsA[minIdx,:]/1000,'-',color='#0571B0',alpha=0.5,markersize=3,label='Min day: %0.1f kWh (%s)' % (np.sum(wattsA[minIdx,:]/1000),datesA[minIdx,0].date()))   
    ax2.fill_between(dts,[0] * len(wattsA[minIdx,:]),wattsA[minIdx,:]/1000, facecolor='#D1E5F0', edgecolor='#D1E5F0',alpha=1 )
    #ax2.plot(dts,wattsA[-1,:]/1000,'-',color='#525252',alpha=0.4,markersize=2,label='last day: %0.1f kWh (%s)' % (np.sum(wattsA[-1,:]/1000),datesA[-1,0].date()))
    
    ax2.xaxis.set_major_locator(mpld.HourLocator(byhour=[0,4,8,12,16,20,24]))
    ax2.xaxis.set_major_formatter(mpld.DateFormatter('%H')) # just the day of week
    for label in ax2.xaxis.get_majorticklabels(): # move the labels into the day range
      label.set_horizontalalignment('left')
    # force 0 as minimum value and make room at the top for the legend
    
    ax2.set_ylim(bottom=0,top=ax2.get_ylim()[1]*1.2)
    ax2.set_title('Highest and lowest days')
    #ax2.set_ylabel('kW')
    #ax2.yaxis.set_ticklabels([])
    ax2.set_xlabel('Hour of day')
    ax2.grid(True,alpha=0.2)
    ax2.legend(loc='upper left',prop={'size':8},markerscale=1)
    ax2.yaxis.set_major_formatter(mplt.FormatStrFormatter('%0.1f'))
    #ax2.set_ylim(ax.get_ylim())
    return fig

  def feature(self,name='range'):
    [datesA,wattsA] = self.building.dailyData

    dayMeans = self.building.dailyStats['mean'] / 1000
    dayMaxes = self.building.dailyStats['max']  / 1000
    dayMins  = self.building.dailyStats['min']  / 1000
    dayRatio = self.building.dailyStats['mxmn']
    dayRange = dayMaxes - dayMins
    plots = [
      (dayMaxes,'max','daily max','kW','',0),
      (dayMins,'min','daily min','kW','',0),
      (dayMeans,'avg','daily average','kW','',0),
      (dayRatio,'max / min ratio','max / min','ratio','',1),
      (dayRange,'range','range: max-min','kW','%m-%y',0),
    ]
    n = len(plots)
    window = 7
    fig = Figure(figsize=(3,6),facecolor='white',edgecolor='none')
    for i,attr in enumerate(plots):
      mn = attr[0].mean()
      ax = fig.add_subplot(n,1,i+1) 
      ax.plot(datesA[(window-1):,0],mlab.movavg(attr[0],window),'-',color='#000000',alpha=1,label=attr[1])
      ax.plot(ax.get_xlim(),[mn,mn],'--',color='b')
      ax.text(.5,0.85,attr[2],weight='bold',  # set the title inside the plot
        horizontalalignment='center',
        fontsize=10,
        transform=ax.transAxes) # makes the location 0-1 for both axes
      ax.text(.5,0.07,'avg=%0.2f' % mn,  # print the mean
        horizontalalignment='center', color='b',
        fontsize=10,
        transform=ax.transAxes) # makes the location 0-1 for both axes
      ax.set_ylabel(attr[3],fontsize=9)
      ax.yaxis.set_major_formatter(mplt.FormatStrFormatter('%0.2f'))
      ax.xaxis.set_major_locator(mpld.MonthLocator(interval=1))
      ax.xaxis.set_major_formatter(mpld.DateFormatter(attr[4]))
      ax.xaxis.grid(True)
      ax.set_ylim(bottom=attr[5],top=ax.get_ylim()[1]*1.2) # anchor max/min at 1:1
      for label in ax.xaxis.get_majorticklabels(): # move the labels into the day range
        label.set_fontsize(8) 
        label.set_rotation(70)
      for label in ax.yaxis.get_majorticklabels(): # move the labels into the day range
        label.set_fontsize(8)
      fig.subplots_adjust(left=0.2)
    return fig

  def writeReadings(self,readings,out=None):
    rows = ['%s,%i' % (reading[0].strftime('%Y-%m-%d %H:%M'),reading[1]) for reading in readings]
    if out is None:
      print rows
    else:
      print "writing to %s" % (out)
      with open(out,'w') as f:
        f.write('date,reading\n')
        for row in rows:
          f.write(row + '\n')

  def saveCSV(self,workDir=None):
    if workDir is None: workDir = self.workDir
    outFile  = os.path.join(workDir,'csv_data.csv')
    self.writeReadings(zip(self.building.data[0],self.building.data[1]),outFile)

  def makeReport(self,workDir=None):
    import subprocess
    if workDir is None: workDir = self.workDir
    template = jinjaEnv.get_template('reportTemplate.html')
    response_dict = {'building' : self.building,
                     'sessionId' : self.sessionId}
    reportHTML = template.render(**response_dict)
    inFile  = os.path.join(workDir,'custom_report.html')
    outFile = os.path.join(workDir,'custom_report.pdf')
    with open(inFile,'wb') as f: f.write(reportHTML)
    subprocess.call([self.wkhtmltopdf,inFile,outFile])
  
  # TODO: See if we want to use this for anything.
  # This example renders plots to PDF directly, with vector graphics.
  def makePDF(self,**params):
    from matplotlib.backends.backend_pdf import PdfPages
    #from pylab import *

    # Create the PdfPages object to which we will save the pages:
    imdata=StringIO()
    # should write to string buffer
    pdf = PdfPages(os.path.join(self.workDir,'figures.pdf')) #imdata) #'multipage_pdf.pdf')

    fig = self.heatmap()
    FigureCanvasPdf(fig) # this constructor sets the figures canvas...
    pdf.savefig(fig)

    fig = self.histogram()
    FigureCanvasPdf(fig) # this constructor sets the figures canvas...
    pdf.savefig(fig) # here's another way - or you could do pdf.savefig(1)

    fig = self.plot()
    FigureCanvasPdf(fig) # this constructor sets the figures canvas...
    pdf.savefig(fig) # or you can pass a Figure object to pdf.savefig

    fig = self.duration()
    FigureCanvasPdf(fig) # this constructor sets the figures canvas...
    pdf.savefig(fig) # or you can pass a Figure object to pdf.savefig

    # We can also set the file's metadata via the PdfPages object:
    d = pdf.infodict()
    d['Title'] =        'Energy Fingerprint Report'
    d['Author'] =       'LBNL Energy Fingerprint Server'
    d['Subject'] =      'Visual summary of energy data with suggestions'
    d['Keywords'] =     'Green Button, Energy data, Fingerprint, LBNL'
    d['CreationDate'] = datetime.datetime.today()
    d['ModDate'] = datetime.datetime.today()

    # Remember to close the object - otherwise the file will not be usable
    pdf.close()
    #return(imdata.getvalue())

  def generateFiles(self,plotName=None,supressException=True):
    plots = [  
       (self.dailyMaxMin,'daily_max_min'),
       (self.heatmap,'heatmap'),
       (self.histogram,'histogram'),
       (self.duration,'load_duration'),
       (self.plot,'plot'),
       (self.meanWeek,'weekly_mean'),
       (self.loadShape,'load_shape'),
       (self.feature,'feature'),
       (self.dailyToutKWh,'tout_vs_kwh'),
    ]
    try:
      try: os.remove(self.errorFile)
      except: pass
      with open(self.lockFile,'wb') as lock: os.utime(self.lockFile,None) # create empty file
      self.saveCSV()
      for (plotFn,fName) in plots:
        if plotName is not None and fName != plotName: continue
        print fName
        self.save(plotFn(),fName,dpi=200)
      if plotName is None: 
        
        self.makeReport()
    except Exception as e: 
      with open(self.errorFile,'wb') as err:
        import traceback
        err.write(str(e))
        traceback.print_exc(file=err)
      if not supressException:
        exc = sys.exc_info()
        raise exc[0], exc[1], exc[2]
    finally: os.remove(self.lockFile)

  def save(self,fig,f=None,dpi=100):
    canvas = FigureCanvasPng(fig)
    canvas.print_figure(os.path.join(self.workDir,f),dpi=dpi)

  def imageData(self,fig):
    canvas=FigureCanvasPng(fig)
    imdata=StringIO()
    canvas.print_png(imdata)
    return imdata.getvalue()

  def pdfData(self,fig):
    canvas=FigureCanvasPdf(fig)
    imdata=StringIO()
    canvas.print_pdf(imdata)
    return imdata.getvalue()

if __name__ == '__main__':
  import sys
  plotName = None
  if len(sys.argv) > 1: # first arg is the file name
    print sys.argv[1]
    plotName = sys.argv[1]
  dataDir = 'c:/dev/fingerprint/file_data/test/'
  dataFile = os.path.join(dataDir,'GB_data.xml')
  b  = Building(parseDataFile(dataFile).getReadings(),94305,{ })
  # PlotMaker is a custom class that consumes building energy data
  # to generate a set of plots to visualize the data
  # It also uses those figures to generate a PDF report - it uses a 
  # html template to format the report and the executable wkhtmltopdf
  # to generate the PDF
  pm = PlotMaker(b,dataDir,'\wkhtmltopdf\wkhtmltopdf.exe')
  pm.generateFiles(plotName,supressException=False) # save out a set of figures to the dataDir
  if plotName is None: pm.makePDF()                 # pdf of figures (vector graphics)


