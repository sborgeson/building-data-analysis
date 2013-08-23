import csv
import datetime as dt
from pytz import timezone
import re

def getInstance(csvFile):
  '''For compatability with code that doesn't know what parser it is getting'''
  return CSVData(csvFile)

class CSVData:
  '''This program parses CSV formatted interval meter data with columns
     'date' as YYYY-MM-DD hh:mm and 'reading' in Watts like this: 
      date,reading
      2011-10-29 00:00,327
      2011-10-29 01:00,267 '''
  def __init__(self,CSVFile):
    with(open(CSVFile,'rb')) as f:
      fReader = csv.reader(f)
      self.headers = fReader.next()
      dateIdx    = 0
      readingIdx = 1
      rows = [(self.parseDate(row[dateIdx]),int(row[readingIdx])) for row in fReader]
      (self.dates,self.rates) = zip(*rows)
      self.data = [self.dates,self.rates]

  def parseDate(self,dateStr):
    # %c is the locale datetime format
    # the rest are permutations on date order and the placement and lengh of year and seconds
    # by no means comprehensive, but hopefully decent coverage of time stamps from different sources
    fmts = ('%c',
            '%Y-%m-%d %H:%M',
            '%m-%d-%Y %H:%M',
            '%m-%d-%y %H:%M',
            '%m/%d/%Y %H:%M',
            '%m/%d/%y %H:%M',
            '%m/%d/%y %I:%M%p',
            '%Y-%m-%d %H:%M:%S',
            '%m-%d-%Y %H:%M:%S',
            '%m-%d-%y %H:%M:%S',
            '%m/%d/%Y %H:%M:%S',
            '%m/%d/%y %H:%M:%S',
            )
    for fmt in fmts:
      try: 
        return dt.datetime.strptime(dateStr,fmt)
      except: pass
    raise ValueError("Can't find a suitable date format to parse CSV data")

  def getReadings(self):
    return self.data

if __name__ == '__main__':
  # TODO: more tests!
  import CSVParse
  a = CSVParse.getInstance('data/sam.csv')
  print a.data
    