# this file uses public sources to determine time series weather data for buildings. This 
# is pieced together using:
# 1) The GreenButton data uploaded by the user. This will define the time range for the weather 
#    data needs and is likely, but not guaranteed to contain zip code and even address data.
# 2) the lat/lon location of a building based on its ZipCode: 
#    see http://jeffreybreen.wordpress.com/2010/12/11/geocode-zip-codes/
# 3) the best match(es) for weather stations by using proximity via lat/lon to WBAN lists: 
#    see ftp://ftp.ncdc.noaa.gov/pub/data/inventories/WBAN.TXT or 
#    the stationsYEARMO.txt files in these http://cdo.ncdc.noaa.gov/qclcd_ascii/
# 4) daily (and optionally hourly and monthly) weather summaries for the appropriate time range
#    also from these: http://cdo.ncdc.noaa.gov/qclcd_ascii/
#    Note that the wclcd files are monthly and therefore requests will span several.
import csv
import os
import urllib
import zipfile
import numpy
import datetime
import math

class WeatherData(object):
  def __init__(self,dataDir):
    self.ZIP_MAP = None # lazy init later. See zipMap

    self.DATA_DIR = dataDir
    self.WBAN_FILE = os.path.join(dataDir,'WBAN.TXT')     # ftp://ftp.ncdc.noaa.gov/pub/data/inventories/WBAN.TXT
                                                          # list of historic through current WBAN stations
    self.ZIP5_FILE = os.path.join(dataDir,'Erle_zipcodes.csv')    # Zip codes with lat/lon from about 2004
                                                                  # see http://jeffreybreen.wordpress.com/2010/12/11/geocode-zip-codes/
    self.NOAA_QCLCD_DATA_DIR = 'http://cdo.ncdc.noaa.gov/qclcd_ascii/'  # Quality controlled local hourly, daily, monthly weather
                                                                        # for the whole US from NOAA. Downloaded 1 month at a time
                                                                        # also contains a station information file.
                                                                        # Naming convention: QCLCD201103.zip
                                                                        # One zip file per month. Current month is a work in progres, 
                                                                        # updated daily
  
  def weatherUrl(self,year,month):  return self.NOAA_QCLCD_DATA_DIR + 'QCLCD%s%02d.zip'  % (year,month)     
  def weatherZip(self,year,month):  return os.path.join(self.DATA_DIR,'QCLCD%s%02d.zip'  % (year,month)) # NOAA zip format for monthly summary data

  def hourlyFile(self,year,month):  return '%s%02dhourly.txt'  % (year,month) # file from within weatherZip
  
  
  def zipMap(self):
    if(self.ZIP_MAP is None): 
      # ['zip', 'city', 'state', 'latitude', 'longitude', 'timezone', 'dst']
      zipList = self.csvData(self.ZIP5_FILE,skip=1)
      self.ZIP_MAP = {}
      for zipRow in zipList:
        self.ZIP_MAP[int(zipRow[0])] = (float(zipRow[3]),float(zipRow[4]))
      print 'Zip to lat/long lookup initialized with %d entries' % len(self.ZIP_MAP)
    return self.ZIP_MAP

  # daily data cols
  #['WBAN', 'YearMonthDay', 'Tmax', 'TmaxFlag', 'Tmin', 'TminFlag', 'Tavg', 'TavgFlag', 
  #'Depart', 'DepartFlag', 'DewPoint', 'DewPointFlag', 'WetBulb', 'WetBulbFlag', 'Heat', 
  #'HeatFlag', 'Cool', 'CoolFlag', 'Sunrise', 'SunriseFlag', 'Sunset', ' SunsetFlag', 
  #'CodeSum', 'CodeSumFlag', 'Depth', 'DepthFlag', 'Water1', 'Water1Flag', 'SnowFall', 
  #'SnowFallFlag', 'PrecipTotal', 'PrecipTotalFlag', 'StnPressure', 'StnPressureFlag', 
  #'SeaLevel', 'SeaLevelFlag', 'ResultSpeed', 'ResultSpeedFlag ', 'ResultDir', 
  #'ResultDirFlag', 'AvgSpeed', 'AvgSpeedFlag', 'Max5Speed', 'Max5SpeedFlag', 'Max5Dir', 
  #'Max5DirFlag', 'Max2Speed', 'Max2SpeedFlag', 'Max2Dir', 'Max2DirFlag']
  def dailyFile(self,year,month):   return '%s%02ddaily.txt'   % (year,month) # file from within weatherZip
  # station data cols
  #['WBAN', 'WMO', 'CallSign', 'ClimateDivisionCode', 'ClimateDivisionStateCode', 
  # 'ClimateDivisionStationCode', 'Name', 'State', 'Location', 'Latitude', 'Longitude', 
  # 'GroundHeight', 'StationHeight', 'Barometer', 'TimeZone']
  def stationFile(self,year,month): return '%s%02dstation.txt' % (year,month) # file from within weatherZip

  # also available: monthly, precip, and remarks
  # checks for file's existence. If not present, downloads and then returns.
  # Note that this will take a while and it is not thread safe in that another. 
  # thread might reproduce the download efforts. So it probably shouldn't
  # be run in the main stream of the code. On the other hand, urlretrieve will
  # probably touch the file in question fairly quickly, and after that others will
  # get read only access to the partial file.
  def confirmedWeatherZip(self,year,month):
    retrieveFile = False
    filePath = self.weatherZip(year,month)
    if os.path.isfile(filePath):
      now      = datetime.datetime.now()
      # empirically, definitive weather files seem to be posted by the 5th or 6th of the next month
      # however, this could result in excessive file downloads. This code should keep it to once a day, 
      # but there are threa safety issues as well as excessive wait times to consider. 
      # We might need to store the modification  time of the file on the server
      postYear  = year
      postMonth = (month + 1) % 13
      if postMonth == 0: 
        postYear = postYear + 1
        postMonth = 1
      postDate = datetime.datetime(postYear,postMonth,7) 
      modTime  = datetime.datetime.fromtimestamp(os.path.getmtime(filePath))
      if postDate >= modTime: # the file was last retrieved before the end of the month it covers
        if now.date() == modTime.date(): pass # we've already dl'd the file today
        else: retrieveFile = True
    else: retrieveFile = True
    if  retrieveFile: # download it and raise exception on failure
      # TODO: do more to protect against race conditions. File lock?
      url = self.weatherUrl(year,month)
      print "%s not found. Attempting download at %s" % (filePath,url)
      urllib.urlretrieve(url,filePath)
      
    return filePath
    
  def csvData(self,filePath,delim=',',colVal=None,subset=None,skip=0):
    with open(filePath,'rb') as f:
      return self.csvDump(f,delim,colVal,subset,skip)

  # optional filter terms col and colVal provide the index of the column
  # and the value to test for returning a subset of the data
  def csvDump(self,f,delim=',',colVal=None,subset=None,skip=0):
    fReader = csv.reader(f,delimiter=delim)
    for i in range(skip): fReader.next() # skip as many rows as requested
    out = None
    if(subset is None):
      if(colVal is None): out = [row for row in fReader if len(row) > 0]
      else: out = [row for row in fReader if row[colVal[0]] == colVal[1] and len(row) > 0]
    else:
      if(colVal is None): out = [[row[i] for i in subset] for row in fReader if len(row) > 0]
      else: out = [[row[i] for i in subset] for row in fReader if row[colVal[0]] == colVal[1] and len(row) > 0]
    return out

  #def csvDumpNumpy(f,delim=',',colVal=None,subset=None):
  #  #out = numpy.genfromtxt(f,delimiter=delim)
  #  out = numpy.loadtxt(f,delimiter=delim,dtype=str)
  #  return out

  #def csvDumpFast(f,delim=',',colVal=None,subset=None):
  #  out = [row.split(delim) for row in f]
  #  return out

  def zippedData(self,filePath,innerFile,delim=',',colVal=None,subset=None,skip=0):
    zf = zipfile.ZipFile(filePath,'r')
    try: return self.csvDump(zf.open(innerFile),delim,colVal,subset,skip)
    finally: zf.close()

  def stationData(self,y,m,colVal=None,subset=None,skip=0): return self.zippedData(self.confirmedWeatherZip(y,m),self.stationFile(y,m),'|',colVal,subset,skip)
  def dailyData(self,y,m,colVal=None,subset=None,skip=0):   return self.zippedData(self.confirmedWeatherZip(y,m),self.dailyFile(y,m),',',colVal,subset,skip)
  def hourlyData(self,y,m,colVal=None,subset=None,skip=0):  return self.zippedData(self.confirmedWeatherZip(y,m),self.hourlyFile(y,m),',',colVal,subset,skip)

  
  # Calculate the distance between two lat/lon locations using the Haversine formula
  def distLatLon(self,lat1,lon1,lat2,lon2):
    """
    Calculate the great circle distance between two points 
    on the earth (specified in decimal degrees)
    """
    # convert decimal degrees to radians 
    lat1,lon1,lat2,lon2 = map(math.radians, [lat1,lon1,lat2,lon2])

    # haversine formula 
    dlon = lon2 - lon1 
    dlat = lat2 - lat1 
    a = math.sin(dlat/2)**2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon/2)**2
    c = 2 * math.asin(math.sqrt(a)) # solid angle between points
    km = 6367 * c
    return km 
  
  # find the WBAN of the weather station closest to the zip code in question
  # using the zip5 lat/lon and the station lat/lon
  # this is potentially diferent every month. Bummer.
  def closestWBAN(self,zip5,y,m,rnk=0):
    return self.stationList(zip5,y,m,n=1)[rnk]

  def stationList(self,zip5,y,m,n=1): # returns the details for the n closest stations to zip5
    if type(zip5) is not int: zip5 = int(zip5)
    zips  = self.zipMap()
    #with Timer('stations'): # this next line takes about 0.038 to run
    stations = self.stationData(y,m,skip=1)
    if len(stations) == 0: 
      print "Warning: no station data for %d/%d, so closest WBAN not found" % (m,y)
      return None # this could be the result of a bad weather file, or just the beginning of the month
    stationMap    = {}
    stationLatLon = {}
    #with Timer('stations'): this code takes about 0.017 to run
    for stationRow in stations:
      try:
        stationLatLon[stationRow[0]] = (float(stationRow[9]),float(stationRow[10]))
        stationMap[stationRow[0]] = stationRow[1:] # details in case we are interested not strictly necessary
      except: 
        print 'bad station data'
        print stationRow
    WBANs = []
    dist = []
    for key in stationLatLon:
      WBANs.append(key)
      dist.append( self.distLatLon( *(zips[zip5] + stationLatLon[key]) ) )
    # get indices of distances in rank order, so we can look at the top N
    rank = sorted(range(len(dist)), key=dist.__getitem__) # sorts the first by the second
    print(rank[0:10])
    warnDist = 15 # km
    bestList = []
    for rnk in range(n):
      wban = WBANs[rank[rnk]]
      if (rnk == 0 and dist[rank[rnk]] > warnDist): print 'WARNING. Closest weather station to %s (WBAN %s) is %0.2fkm away %s' % (zip5,wban,dist[rank[rnk]],str(stationMap[ wban ] ))
      #print([dist[i] for i in rank[0:10]]) # top ten distances
      #print([WBANs[i] for i in rank[0:10]]) # top ten stations
      #print stationMap[bestWBAN] # station details
      
      entry = [wban,dist[rank[rnk]]]
      entry.extend(stationMap[wban])
      bestList.append(entry)
    return bestList
  
  # return daily weather data for the zip code and month in question
  def weatherMonth(self,zip5,y,m,subset=[0,1,2,4,6]): # cols for WBAN,date,tmax,tmin,tavg
    weather = []
    stationList = self.stationList(zip5,y,m,5)
    if stationList is None: return []
    for rnk in range(5):
      cWB = stationList[rnk]
      print cWB
      if cWB is None: return []
      bestWBAN = cWB[0]
      dist     = cWB[1]
      #print 'best WBAN:', bestWBAN
      weather = self.dailyData(y,m,colVal=(0,bestWBAN),subset=subset) # filter by WBAN
      if len(weather) > 0: break # we found data
      # otherwise try again because the current stationhas no data
    return weather

  def weatherRange(self,zip5,start,end,subset=[0,1,2,4,6]): # cols for WBAN,date,tmax,tmin,tavg
    weather = []
    for year in range(start.year,end.year+1):
      sMon = 1
      eMon = 12
      if year == start.year: sMon = start.month
      if year == end.year:   eMon = end.month
      for mon in range(sMon,eMon+1):
        #with Timer('weatherMonth'): # each month takes about 1.5-1.6 seconds
        print year,mon
        weather.extend(self.weatherMonth(zip5,year,mon,subset))
    return weather

  # get the touts for the dates passed in
  def matchWeather(self,dates,zip5):
    if type(dates[0]) == datetime.datetime:
      dates = [x.date() for x in dates] # this thing runs of datetime.date objects
    start = dates[0]
    end = dates[-1]
    weather = self.weatherRange(zip5,start,end,[1,6])
    wa = numpy.array(weather)
    wDates = [datetime.datetime.strptime(dateStr,'%Y%m%d').date() for dateStr in wa[:,0]]
    tout   = [floatParse(x) for x in wa[:,1]]
    md = self.matchDates(dates,wDates) # get the indices for matching dates for both arrays (md[0] is dates and md[1] is wDates
    matches = [numpy.nan] * len(dates) # populate an empty array
    for i in range(len(md[0])):   
      matches[md[0][i]] = tout[md[1][i]] # fill in just the values for which the dates match, at the locations of the first date argument
    return (dates,matches)

  # find the indices for each list where they share the same values
  def matchDates(self,dates,wDates):
    j = 0 # index of wDates
    wIdx = []
    dIdx = []
    try:
      for i,d in enumerate(dates):
        while wDates[j] < d: j = j + 1
        if wDates[j] == d: 
          wIdx.append(j)
          dIdx.append(i)
    except IndexError as ie: pass # if we run out of wDates, the loop should end
    #print wIdx
    #print dIdx
    #print [dates[i] for i in dIdx]
    return(dIdx,wIdx)

# Utility timer class. Usage:
#with Timer('foo_stuff'):
#  do stuff
# then it prints out the elapsed time
import time
class Timer(object):
  def __init__(self, name=None):
    self.name = name

  def __enter__(self):
    self.tstart = time.time()

  def __exit__(self, type, value, traceback):
    if self.name:
      print '[%s]' % self.name,
    print 'Elapsed: %s' % (time.time() - self.tstart)

def floatParse(string, fail=numpy.nan):
    try:              return float(string)
    except Exception: return fail;

if __name__ == '__main__':
  wd = WeatherData('weather') # dataDir
  zip5 = 94305
  zips  = wd.zipMap()
  print(zips[zip5])
  with Timer('weather'):
    start = datetime.datetime(2013,3,1)
    end   = datetime.datetime(2013,4,21)
    dt = datetime.timedelta(days=1)
    dates = [start + x * dt for x in range((end-start).days)]
    (dates,tout) = wd.matchWeather(dates,zip5)
    #weather = wd.weatherRange(zip5,start,end)
    #print(weather[0])
    #wa = numpy.array(weather)
    #dates = [datetime.datetime.strptime(dateStr,'%Y%m%d') for dateStr in wa[:,1]]
    #tout  = [floatParse(x) for x in wa[:,4]]
    #print dates
    print tout

    #print(weather[0])
  
  #with Timer('all'):
  #  weather      = wd.dailyData(2013,03) # all data
  #print(len(weather),len(weather[0]))
  #with Timer('filter'):
  #  weatherWBAN  = wd.dailyData(2013,03,colVal=(0,'03013')) # filter by WBAN
  #print(len(weatherWBAN),len(weatherWBAN[0]))
  #with Timer('sub'):
  #  weatherSub  = wd.dailyData(2013,03,subset=[0,1,2,4,6]) # filter by WBAN and col for WBAN, date, tmax,tmin,tavg
  #print(len(weatherSub),len(weatherSub[0]))
  #with Timer('sub filter'):
  #  weatherSubWBAN  = wd.dailyData(2013,03,colVal=(0,'03013'),subset=[0,1,2,4,6]) # filter by WBAN and col for WBAN, date, tmax,tmin,tavg
  #print(len(weatherSubWBAN),len(weatherSubWBAN[0]))
