from math import *
from dqmjson import *
from ROOT import TFile
from optparse import OptionParser
from xml.dom.minidom import parseString
from rrapi import RRApi, RRApiError
import xmlrpclib
#import elementtree.ElementTree as ET
import sys, os, os.path, time, re, subprocess
import urllib
import json

##Run classification
groupName = "Collisions16"
#groupName = "Commissioning2016"
##Dataset for GUI query
express = ['/StreamExpress/', '/StreamExpressCosmics/']
expresshi = ['/StreamHIExpress/', '/StreamExpressCosmics/']
prompt  = ['/ZeroBias/',   '/Cosmics/']
prompt1  = ['/ZeroBias1/',   '/Cosmics/']
prompthi  = ['/HIMinimumBias1/',   '/Cosmics/']
express0t = ['/StreamExpress0T/', '/StreamExpressCosmics/']
prompt0t  = ['/ZeroBias_0T/',   '/Cosmics/']
yearPattern = ".*16" # select anything with a 16 in the name
##Workspace and dset types
Wkspace = ["GLOBAL", "TRACKER"]
####This is under construction...
##Recotype= ["Online", "Prompt"]
##Selection of GUI query array element
Dtype = 0

### List of people who are not shifters, and whose open runs should be considered "TODO"
NonShifters = [ "DQMGUI Trigger" ] 

####Cosmics settings are set after loading config options#####

os.environ['X509_USER_CERT']='/data/users/11.11a/auth/proxy/proxy.cert'
parser = OptionParser()
parser.add_option("-c", "--cosmics", dest="cosmics", action="store_true",  default=False, help="Check cosmic instead of collision")
parser.add_option("-m", "--min", dest="min", type="int", default=0,      help="Minimum run")
parser.add_option("-M", "--max", dest="max", type="int", default=999999, help="Maximum run")
parser.add_option("--min-ls",    dest="minls",  type="int", default="10",   help="Ignore runs with less than X lumis (default 10)")
parser.add_option("-v", "--verbose", dest="verbose", action="store_true",  default=False, help="Print more info")
parser.add_option("-p", "--pretend", dest="pretend", action="store_true",  default=False, help="Use cached RR result")
parser.add_option("-f", "--force", dest="force", action="store_true",  default=False, help="Never cached RR result")
parser.add_option("-n", "--notes", dest="notes", type="string", default="notes.txt", help="Text file with notes")
(options, args) = parser.parse_args()

eras = []
erafile = open("eras.txt","r")
for line in erafile:
    if "from" in line: continue
    cols = line.split(); 
    if len(cols) != 5: continue
    eras.append( ( int(cols[0]), int(cols[1]), cols[2], cols[3], cols[4] ) )

def eraForRun(run):
    for min,max,era,pr,er in eras:
        if run >= min and run <= max: return era
    return "Unknown"
def getPrForRun(run):
    for min,max,era,pr,er in eras:
        if run >= min and run <= max: return pr
    return "Unknown"
def getErForRun(run):
    for min,max,era,pr,er in eras:
        if run >= min and run <= max: return er
    return "Unknown"

def isExpressDoneInGUI(run):
    dataset = "%s%s-%s/DQMIO" % (express[Dtype], eraForRun(run), getErForRun(run))
    try:
        info = dqm_get_json(serverurl, run, dataset, "Info/ProvInfo")
        done = info['runIsComplete']['value']
        return done == '1'
    except:
        return False 
    return False

def truncate(f, n):
    '''Truncates/pads a float f to n decimal places without rounding'''
    s = '{}'.format(f)
    if 'e' in s or 'E' in s:
        return '{0:.{1}f}'.format(f, n)
    i, p, d = s.partition('.')
    return '.'.join([i, (d+'0'*n)[:n]])

##Cosmic settings...
if options.cosmics: groupName = "Cosmics16"
##NOTE: Currently using prompt stream (not express) for central data certification
if options.cosmics: Dtype = 1

notes = {}
if options.notes:
    try:
        nfile = open(options.notes, "r");
        for l in nfile:
            m = re.match(r"\s*(\d+)\s*:?\s+(.*)", l)
            if m:
                notes[int(m.group(1))] = m.group(2)
    except IOError:
        print "Couldn't read notes file", options.notes, "Will use Tracker Prompt RECO comments instead"

lumiCache = {}; 
lumiCacheName = "lumi-by-run.txt" if not options.cosmics else "tracks-by-run.txt"
try:
    lumiFile = open(lumiCacheName, "r")
    for l in lumiFile:
        m = re.match(r"(\d+)\s+(\d+)\s+([0-9.]+).*", l)
        if m:
            if not options.cosmics:
                lumiCache[int(m.group(1))] = int(m.group(2)), float(m.group(3))
            else:
                cols = l.split()
                lumiCache[int(cols[0])] = [ int(cols[1]), int(cols[2]), cols[3], cols[4], cols[5].replace("_"," ") ] 
    print "LUMICACHE " , lumiCache
except IOError:
   pass 

runlist = {}

URL = 'http://runregistry.web.cern.ch/runregistry/'
api = RRApi(URL, debug = False)

def getRR(whichRR, dataName):
    global groupName, runreg, runlist, options
    sys.stderr.write("Querying %s RunRegistry for %s runs...\n" % (whichRR,dataName));
    mycolumns = ['pix','strip','track','ranges','runNumber','datasetState','lastShifter']
    text = ''
    fname = "RR_%s.%s.%s.xml" % (whichRR,groupName,dataName)
    readFile = os.path.exists(fname) and options.pretend
    if os.path.exists(fname) and (time.time() - os.stat(fname).st_mtime) < 10*60 and not options.force:
        readFile = True
    if readFile:
        if options.verbose: print "  will read from %s (%.0f minutes old)" % (fname, (time.time() - os.stat(fname).st_mtime)/60)
        log = open(fname); 
        text = "\n".join([x for x in log])
    else:
        ##Query RR
        if api.app == "user":
            text = api.data(workspace = whichRR, table = 'datasets', template = 'xml', columns = mycolumns, filter = {'runNumber':'>= %d and <= %d'%(options.min,options.max),'runClassName':"like '%%%s%%'"%groupName,'datasetName':"like '%%%s%%'"%dataName})
        log = open(fname,"w"); 
        log.write(text); log.close()
    ##Get and Loop over xml data
    dom = ''; domP = None
    domB = '';
    try:
        dom  = parseString(text)
        print "DOM " , dom
    except:
        ##In case of a non-Standard RR output (dom not set)
        print "Could not parse RR output"
    if whichRR == "GLOBAL" and dataName == "Online": 
        text_bfield = api.data(workspace = 'GLOBAL', table = 'runsummary', template = 'xml', columns = ['number','bfield'], filter = {"runClassName": "like '%%%s%%'"%groupName, "number": ">= %d AND <= %d" %(options.min,options.max), "datasets": {"rowClass": "org.cern.cms.dqm.runregistry.user.model.RunDatasetRowGlobal", "datasetName": "like %Online%"}}, tag= 'LATEST')
        log = open("RR_bfield.xml","w");
        log.write(text_bfield); log.close()
        try:
            domB  = parseString(text_bfield)
            print "DOMB " , domB
        except:
        ##In case of a non-Standard RR output (dom not set)
            print "Could not parse RR output"

    if os.path.exists("patches/"+fname):
        try:
            domP = parseString( "\n".join([x for x in open("patches/"+fname)]) )    
            print "Found manual patch of RR ",fname
        except:
            pass
    splitRows = 'RunDatasetRowTracker'
    if whichRR == 'GLOBAL': splitRows = 'RunDatasetRowGlobal'
    ##Protection against null return
    if dom: data = dom.getElementsByTagName(splitRows)
    else: data =[]
    if domP: dataP = domP.getElementsByTagName(splitRows)
    else: dataP =[]
    if domB: dataB = domB.getElementsByTagName('RunSummaryRowGlobal')
    else: dataB =[]
    for i in range(len(data)):
        ##Get run#
        run = int(data[i].getElementsByTagName('runNumber')[0].firstChild.data)
        if run < options.min: continue
        if run > options.max: continue
        mydata = data[i]
        for X in dataP:
            if int(X.getElementsByTagName('runNumber')[0].firstChild.data) == run:
                print "Run ",run, ": found manual patch for ",whichRR,groupName,dataName,
                mydata = X; break
        state = mydata.getElementsByTagName('datasetState')[0].firstChild.data
        shifter = mydata.getElementsByTagName('lastShifter')[0].firstChild.data
        isopen = (state  == "OPEN")
        lumis= 0
        bfield = -1
        for X in dataB:
            if int(X.getElementsByTagName('number')[0].firstChild.data) == run:
                bfield = X.getElementsByTagName('bfield')[0].firstChild.data
                break
        if run not in runlist: runlist[run] = {'ls':lumis}
        ### PIXEL
        goodp = mydata.getElementsByTagName(mycolumns[0])[0].getElementsByTagName('status')[0].firstChild.data == 'GOOD'
        commp = (mydata.getElementsByTagName(mycolumns[0])[0].getElementsByTagName('comment')[0].toxml()).replace('<comment>','').replace('</comment>','').replace('<comment/>','')
        ### STRIP
        goods = mydata.getElementsByTagName(mycolumns[1])[0].getElementsByTagName('status')[0].firstChild.data == 'GOOD'
        comms = (mydata.getElementsByTagName(mycolumns[1])[0].getElementsByTagName('comment')[0].toxml()).replace('<comment>','').replace('</comment>','').replace('<comment/>','')
        ##No tracking flag for 'Global'/'Online', cosmic data good if strips good...
        if options.cosmics:
            goodt = (goods); commt = ""
        else:
            goodt = (goods and goodp); commt = ""
        if whichRR != 'GLOBAL' and dataName != 'Online':
            ### TRACKING
            goodt = mydata.getElementsByTagName(mycolumns[2])[0].getElementsByTagName('status')[0].firstChild.data == 'GOOD'
            commt = (mydata.getElementsByTagName(mycolumns[2])[0].getElementsByTagName('comment')[0].toxml()).replace('<comment>','').replace('</comment>','').replace('<comment/>','')
        if goodt:
            verdict = "GOOD"
            if not goodp: verdict += ", px bad"
            if not goods: verdict += ", st bad"
        else:
            verdict = 'BAD'
            if goodp: verdict += ", px good" 
            if goods: verdict += ", st good" 
        if options.verbose: print "  -",run,lumis,verdict
        ##Compile comments
        comment = ""
        if commt: comment += commt
        if comms: comment += ", strip: "+comms
        if commp: comment += ", pixel: "+commp
        if isopen and shifter in NonShifters: (isopen, verdict,comment) = (True, "TODO","")
        runlist[run]['RR_'+whichRR+"_"+dataName] = [ isopen, verdict, comment ]
        if whichRR == 'GLOBAL' and dataName == 'Online':
            runlist[run]['RR_bfield'] = float(bfield)
            
        #print "runlist " , runlist[run]

getRR("GLOBAL", "Online")
getRR("GLOBAL", "Prompt")
getRR("TRACKER", "Express")
getRR("TRACKER", "Prompt")
##Start running RR queries
#for work in Wkspace:
#    for reco in Recotype:
#        getRR(work,reco)
        #if options.cosmics and work == "GLOBAL" and reco == "Prompt": getRR(work,"Express")
        #else: getRR(work,reco)

print "Querying runs from DQM GUI"
ed = express[Dtype]
pd = prompt[Dtype]
pd1 = prompt1[Dtype]
pd0t = prompt0t[Dtype]
pdhi = prompthi[Dtype]
ed0t = express0t[Dtype]
edhi = expresshi[Dtype]

for n,d in (('Express',ed), ('Prompt',pd)):
    samples = dqm_get_samples(serverurl, d+yearPattern)
    for (r, d2) in samples:
        if r not in runlist: continue
        runlist[r]['GUI_'+n] = True

if Dtype == 0: #collisions-only
    for n,d in (('Express',ed), ('Prompt',pd1)):
        samples = dqm_get_samples(serverurl, d+yearPattern)
        for (r, d2) in samples:
            if r not in runlist: continue
            runlist[r]['GUI_'+n] = True

    for n,d in (('Express',ed0t), ('Prompt',pd0t)):
        samples = dqm_get_samples(serverurl, d+yearPattern)
        for (r, d2) in samples:
            if r not in runlist: continue
            runlist[r]['GUI_'+n] = True

    for n,d in (('Express',edhi), ('Prompt',pdhi)):
        samples = dqm_get_samples(serverurl, d+yearPattern)
        for (r, d2) in samples:
            if r not in runlist: continue
            runlist[r]['GUI_'+n] = True

if not options.cosmics:
    print "Getting luminosities"
    newcache = open("lumi-by-run.txt", "w");
    newcache.write("run\tls\tlumi_pb\n");
    for run in runlist.keys():
        if run not in lumiCache:
            print " - ",run
            lslumi = (-1,0)
            try:
                os.system("./lumiCalc2_wrapper.sh %d" % run)
                out = [ l for l in open("lumi.tmp","r")]
                if (len(out) <= 1): raise ValueError
                ##quick fix for multiple LS intervals...
                out[1] = out[1].replace("], [", "]; [")
                cols = out[1].strip().split(",");
                print cols
                (myrun,myls,delivered,sells,mylumi) = out[1].strip().split(",")
                myrun = myrun.split(":")[0]
                if int(myrun) == run:
                    lslumi = ( int(myls), float(mylumi)/1.0e6 )
                    if options.verbose: print "\t- %6d, %4d, %6.3f" % (run, lslumi[0], lslumi[1])
            except IOError:
                pass
            except ValueError:
                lslumi = (-1,0)

            try:
                dataset = "%s%s-%s/DQMIO" % (express[0], eraForRun(run), getErForRun(run))
                print dataset
                ei = dqm_get_json(serverurl, run, dataset, "Info/EventInfo")
                myls = ei['ProcessedLS']['nentries']
                lslumi = ( int(myls), 0 )
            except:
                pass

            lumiCache[run] = lslumi
        if lumiCache[run][0] != -1:
            newcache.write("%d\t%d\t%.3f\n" % (run, lumiCache[run][0], lumiCache[run][1]))
    newcache.close()
else:
    #print "Getting APV modes"
    #apvModeList = []; minrun = min(runlist.keys())
    #pyScript = os.environ['CMSSW_RELEASE_BASE']+"/src/CondFormats/SiStripObjects/test/SiStripLatencyInspector.py"
    #pyScript = "SiStripLatencyInspector.py"
    #modeDumpPipe = subprocess.Popen(['python', pyScript], bufsize=-1, stdout=subprocess.PIPE).stdout;
    #for line in modeDumpPipe:
    #    m = re.match(r"since = (\d+) , till = (\d+) --> (peak|deco) mode", line)
    #    if m:
    #        first, last, mode = int(m.group(1)), int(m.group(2)), m.group(3).upper() 
    #        if last >= minrun: apvModeList.append( (first, last, mode) )
    #apvModeList.sort()
    print "Getting tracks"
    newcache = open("tracks-by-run.txt", "w");
    newcache.write("run\tls\talcatracks\tmode\tmode_flag\tmode_text\n");
    for run in runlist.keys():
        if run not in lumiCache:
            print " - ",run
            dbmode = '???'
            #for (start,end,mode) in apvModeList:
            #    if run >= start and run <= end: 
            #        dbmode = mode
            #        break

            link = "http://cern.ch/erik.butz/cgi-bin/getReadOutmode.pl?RUN=" + str(run)
            f = urllib.urlopen(link)
            json_data = f.read()            
            dbmodelist = json.loads(json_data)
            dbmode = dbmodelist[0][1]
            lslumi = (-1,0,dbmode,"WAIT","from DB mode (run not in prompt GUI yet)")
            try:
                dataset = "%s%s-%s/DQMIO" % (prompt[1], eraForRun(run), getPrForRun(run))
                print "DATASET " , dataset
                at = dqm_get_json(serverurl, run, dataset, "AlCaReco/TkAlCosmics0T/GeneralProperties")
                ei = dqm_get_json(serverurl, run, dataset, "Info/EventInfo")
                tib =dqm_get_json(serverurl, run, dataset, "SiStrip/MechanicalView/TIB")
                nlumis  = ei['ProcessedLS']['nentries']
                nalcatracks = at['Chi2Prob_ALCARECOTkAlCosmicsCTF0T']['nentries']
                ston_num = tib['Summary_ClusterStoNCorr_OnTrack__TIB']['nentries']
                ston_avg = tib['Summary_ClusterStoNCorr_OnTrack__TIB']['stats']['x']['mean']
                mode = "???"; mode_flag = 'bah'; mode_text = 'not found'
                if ston_num > 100:
                    if 28 < ston_avg and ston_avg < 35: mode, mode_flag, mode_text = "PEAK", "TODO", "from S/N plot";
                    if 18 < ston_avg and ston_avg < 24: mode, mode_flag, mode_text = "DECO", "TODO", "from S/N plot";
                if mode == dbmode:  mode, mode_flag, mode_text = dbmode, "GOOD","from both DB and S/N"
                elif mode == "???": mode, mode_flag, mode_text = dbmode, "WAIT","from DB only (S/N info is inconclusive)"
                else: mode, mode_flag, mode_text = dbmode+"?", "BAD","DB says %s, but mean S/N = %.1f suggests %s" % (dbmode,ston_avg,mode)
                lslumi = (nlumis, nalcatracks, mode, mode_flag, mode_text)
            except:
                pass
            if lslumi[1] == 0:
                try:
                    dataset = "%s%s-%s/DQMIO" % (express[1], eraForRun(run), getErForRun(run))
                    at = dqm_get_json(serverurl, run, dataset, "AlCaReco/TkAlCosmics0T/GeneralProperties")
                    ei = dqm_get_json(serverurl, run, dataset, "Info/EventInfo")
                    nlumis  = ei['ProcessedLS']['nentries']
                    nalcatracks = at['Chi2Prob_ALCARECOTkAlCosmicsCTF0T']['nentries']
                    if nlumis > 0:
                        lslumi = (-nlumis,nalcatracks,dbmode,"WAIT","from DB mode (run not in prompt GUI yet)")
                except:
                    pass
            print "LSLUMI ", lslumi
            lumiCache[run] = lslumi
        if lumiCache[run][0] >= 0:
            newcache.write("%d\t%d\t%d\t%s\t%s\t%s\n" % (run, 
                lumiCache[run][0], lumiCache[run][1], 
                lumiCache[run][2], lumiCache[run][3], lumiCache[run][4].replace(" ","_")))
    newcache.close()

print "Done"

html = """
<html>
<head>
  <title>Certification Status, %s (%s)</title>
  <style type='text/css'>
    body { font-family: "Candara", sans-serif; }
    td.BAD { background-color: rgb(255,100,100); }
    td.bah { background-color: rgb(255,180,80); }
    td.GOOD { background-color: rgb(100,255,100); }
    td.TODO { background-color: yellow; }
    td.WAIT { background-color: rgb(200,200,255); }
    td.Wait { background-color: rgb(200,230,255); }
    td.SKIP { background-color: rgb(200,200,200); }
    td, th { padding: 1px 5px; 
             background-color: rgb(200,200,200); 
             margin: 0 0;  }
    td.num { text-align: right; padding: 1px 10px; }
    table, tr { background-color: black; }
  </style>
</head>
<body>
<h1>Certification Status, %s (%s)</h1>
<table>
""" % (groupName, time.ctime(), groupName, time.ctime())
if not options.cosmics:
    html += "<tr><th>Run</th><th>B-field</th><th>LS</th><th>LUMI</th><th>ONLINE</th><th>EXPRESS</th><th>PROMPT</th><th>CENTRAL</th><th>NOTES</th></tr>"
else:
    html += "<tr><th>Run</th><th>B-field</th><th>LS</th><th>TRACKS<br/>ALCA</th><th>TRACK RATE<br/>ALCA</th><th>APV<br/>MODE</th><th>ONLINE</th><th>EXPRESS</th><th>PROMPT</th><th>CENTRAL</th><th>NOTES</th></tr>"

def v2c(isopen,verdict):
    if isopen: return 'TODO'
    for X,Y in [('BAD','BAD'), ('bad','bad'), ('GOOD','GOOD'), ('TODO','TODO'), ('WAIT','WAIT'), ('Wait','Wait'),('SKIP','SKIP'),('N/A','SKIP'),('STANDBY','STANDBY'),('EXCLUDED','EXCL')]:
        if X in verdict: return Y
def p2t(pair):
    (isopen, verdict, comment) = pair
    if comment:
        return "%s <span title=\"%s\">[...]</span>" % (verdict, comment)
    else:
        return verdict

allLumi_currentH=0
allAlcaTracks_currentH=0
allLumiWait=0
allTracksWait=0
maxcosmicrunforstat = 0
allAlcaTracksPEAK=0

allLumiB=0
allAlcaTracksB=0
allLumiC=0
allAlcaTracksC=0
allLumiD=0
allAlcaTracksD=0
allLumiE=0
allAlcaTracksE=0
allLumiF=0
allAlcaTracksF=0
allLumiG=0
allAlcaTracksG=0

runs = runlist.keys(); runs.sort(); runs.reverse()
print "ALL RUNS: " , runs , "\n"
if options.cosmics: print "ONLINE: Global Online, EXPRESS: Trk Online, PROMPT: Trk Prompt, CENTRAL: Global ExpressStream"
else: print "ONLINE: Global Online, EXPRESS: Trk Online, PROMPT: Trk Prompt, CENTRAL: Global Prompt"
print ""
print "%-6s |  %-15s | %-15s | %-15s | %-15s | %s " % ("RUN","ONLINE","EXPRESS","PROMPT","CENTRAL","NOTES")
print "%-6s |  %-15s | %-15s | %-15s | %-15s | %s " % ("-"*6, "-"*15, "-"*15, "-"*15, "-"*15, "-"*30)
for r in runs:
    #if options.cosmics and lumiCache[r][3] == 'WAIT': continue #ignore cosmic runs in the waiting list (express stream)
    if options.cosmics and lumiCache[r][0] == -1: continue     #ignore irrelevant runs (?)
    R = runlist[r]
    print ' R '
    print R
    All_comments=''
    online = R['RR_GLOBAL_Online'] if 'RR_GLOBAL_Online' in R else [False,'TODO','']
    (expr_t, prompt_t, central) = ([False,'WAIT',''], [False,'WAIT',''], [False,'WAIT',''])
    #if 'GUI_Express' in R:
    if not 'RR_TRACKER_Express' in R:
        if isExpressDoneInGUI(r):
            expr_t = [ False, 'TODO','' ]
    if 'RR_TRACKER_Express' in R:
        expr_t = R['RR_TRACKER_Express'] if 'RR_TRACKER_Express' in R else [False,'TODO',''];
        if options.cosmics:
            #expr_t = [ False, 'N/A','' ]
            print "COSMICS" , expr_t
        elif expr_t[1] == 'TODO' and not isExpressDoneInGUI(r):
             expr_t = [ False, 'Wait','Express not complete in GUI yet' ]
    print 'EXPRT' , expr_t         
    if not options.cosmics and (expr_t[1] == 'Wait' or expr_t[1] == 'WAIT'): continue #ignore collision runs in the waiting list
    #if 'GUI_Prompt' in R:
    if 'RR_TRACKER_Prompt' in R:
        prompt_t = R['RR_TRACKER_Prompt'] if 'RR_TRACKER_Prompt' in R else [False,'TODO',''];
        All_comments+= prompt_t[1]
        central = R['RR_GLOBAL_Prompt']  if 'RR_GLOBAL_Prompt'  in R else [False,'TODO',''];
    note = notes[r] if r in notes else All_comments
    print prompt_t
    print "%6d |  %-15s | %-15s | %-15s | %-15s | %s " % (r, online[1], expr_t[1], prompt_t[1], central[1], note)
    if not options.cosmics:
        html += "<tr><th>%d</th><td class='num'>%.1f T</td><td class='num'>%d</td><td class='num'>%.1f pb<sup>-1</sup></td>" % (r, runlist[r]['RR_bfield'] , lumiCache[r][0], lumiCache[r][1])
    else:
        if lumiCache[r][0] >= 0:
            html += "<tr><th>%d</th><td class='num'>%.1f T</td><td class='num'>%d</td><td class='num'>%d</td><td class='num'>%.1f Hz</td>" % (r, runlist[r]['RR_bfield'], lumiCache[r][0], lumiCache[r][1], lumiCache[r][1]/lumiCache[r][0]/23.31 )
        else:
            html += "<tr><th>%d</th><td class='num'>%.1f T</td><td class='num TODO'>%d</td><td class='num TODO'>%d</td><td class='num TODO'>%.1f Hz</td>" % (r, runlist[r]['RR_bfield'], -lumiCache[r][0], lumiCache[r][1], -lumiCache[r][1]/lumiCache[r][0]/23.31)
        html += "<td class='%s'><span title='%s'>%s</span></td>" % (lumiCache[r][3], lumiCache[r][4], lumiCache[r][2])

    if not options.cosmics:
        for X in (online, expr_t, prompt_t, central):
            html += "<td class='%s'>%s</td>" % (v2c(X[0],X[1]), p2t(X))
    else:
        position=0
        for X in (online, expr_t , prompt_t, central):
            html += "<td class='%s'>%s</td>" % (v2c(X[0],X[1]), p2t(X))
            position=position+1
            if position == 3 and options.cosmics and abs(lumiCache[r][0]) > 1: #('BAD' not in X[1])
                if r >= 280992 and runlist[r]['RR_bfield'] > 3.6: #3.8T cosmics                
                        allLumi_currentH=allLumi_currentH+abs(lumiCache[r][0])
                        allAlcaTracks_currentH=allAlcaTracks_currentH+abs(lumiCache[r][1])
                        maxcosmicrunforstat = max(maxcosmicrunforstat, r)

                if r >= 278809 and r <= 280831 and runlist[r]['RR_bfield'] > 3.6: #3.8T cosmics                
                        allLumiG=allLumiG+abs(lumiCache[r][0])
                        allAlcaTracksG=allAlcaTracksG+abs(lumiCache[r][1])

                if r >= 277772 and r <= 278808 and runlist[r]['RR_bfield'] > 3.6: #3.8T cosmics                
                        allLumiF=allLumiF+abs(lumiCache[r][0])
                        allAlcaTracksF=allAlcaTracksF+abs(lumiCache[r][1])

                if r >= 276824 and r <= 277754 and runlist[r]['RR_bfield'] > 3.6: #3.8T cosmics                
                        allLumiE=allLumiE+abs(lumiCache[r][0])
                        allAlcaTracksE=allAlcaTracksE+abs(lumiCache[r][1])

                if r >= 276333 and r <= 276804 and runlist[r]['RR_bfield'] > 3.6: #3.8T cosmics                
                        allLumiD=allLumiD+abs(lumiCache[r][0])
                        allAlcaTracksD=allAlcaTracksD+abs(lumiCache[r][1])

                if r >= 275419 and r <= 276825 and runlist[r]['RR_bfield'] > 3.6: #3.8T cosmics                
                        allLumiC=allLumiC+abs(lumiCache[r][0])
                        allAlcaTracksC=allAlcaTracksC+abs(lumiCache[r][1])

                if r >= 272118 and r <= 275418 and runlist[r]['RR_bfield'] > 3.6: #3.8T cosmics                
                        allLumiB=allLumiB+abs(lumiCache[r][0])
                        allAlcaTracksB=allAlcaTracksB+abs(lumiCache[r][1])

                        #if lumiCache[r][2] == 'PEAK':
                        #    allAlcaTracksPEAK = allAlcaTracksPEAK + abs(lumiCache[r][1])
                    #else:
                    #    allLumiWait=allLumiWait+abs(lumiCache[r][0])
                    #    allTracksWait=allTracksWait+abs(lumiCache[r][1])

    html += "<td>%s</td></tr>\n" % note;

html += "</table></body></html>"

out = open("status.%s.html" % groupName, "w")
out.write(html.encode('utf-8')) #prevent crashes when special chars somehow enter description in RR
out.close()

if options.cosmics: 
    #print "total lumi: " , allLumi , " ALCA tracks: " , allAlcaTracks , " hours: " , allLumi * 23.31 / 3600.
    #print "lumi tracks WAIT: " , allLumiWait , " " , allTracksWait

    htmlCOSMICTRACKS = """
        <!DOCTYPE html>
        <html lang="en">
          <head>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <title>Cosmic Tracks Summary</title>

            <!-- Bootstrap -->
            <link rel="stylesheet" type="text/css" href="css/bootstrap.min.css">

            <!-- Main Style -->
            <link rel="stylesheet" type="text/css" href="css/main.css">
          </head>

        <body>
        <section id="text-about">
            2016 ALCARECO cosmic tracks (%s):
        </section>
        <section id="my-table">
            <div class="container">

                <div class="row">
                    <div class="main">

                        <div class="col-md-4 col-sm-12 col-xs-12">
                            <div class="my-table">
                                <div class="table-header">
                                    <p class="table-title">2016H</p>
                                    <p class="table-tracks"><sup>ALCA tracks</sup> %.0fK <span>@ 3.8T</span></p>
                                </div>

                                <div class="table-details">
                                    <ul>
                                        <li>run range: 280992 - %i</li>
                                        <li>%i hours and counting...</li>
                                    </ul>
                                </div>
                            </div>
                        </div>

                        <div class="col-md-4 col-sm-12 col-xs-12">
                            <div class="my-table">
                                <div class="table-header">
                                    <p class="table-title">2016G</p>
                                    <p class="table-tracks"><sup>ALCA tracks</sup> 356K <span>@ 3.8T</span></p>
                                </div>

                                <div class="table-details">
                                    <ul>
                                        <li>run range: 278809 - 280831</li>
                                        <li>51 hours in total</li>
                                    </ul>
                                </div>
                            </div>
                        </div>

                        <div class="col-md-4 col-sm-12 col-xs-12">
                            <div class="my-table">
                                <div class="table-header">
                                    <p class="table-title">2016F</p>
                                    <p class="table-tracks"><sup>ALCA tracks</sup> 495K <span>@ 3.8T</span></p>
                                </div>

                                <div class="table-details">
                                    <ul>
                                        <li>run range: 277772 - 278808</li>
                                        <li>68 hours in total</li>
                                    </ul>
                                </div>
                            </div>
                        </div>

                        <div class="col-md-4 col-sm-12 col-xs-12">
                            <div class="my-table">
                                <div class="table-header">
                                    <p class="table-title">2016E</p>
                                    <p class="table-tracks"><sup>ALCA tracks</sup> 46K <span>@ 3.8T</span></p>
                                </div>

                                <div class="table-details">
                                    <ul>
                                        <li>run range: 276824 - 277754</li>
                                        <li>12 hours in total</li>
                                    </ul>
                                </div>
                            </div>
                        </div>

                        <div class="col-md-4 col-sm-12 col-xs-12">
                            <div class="my-table">
                                <div class="table-header">
                                    <p class="table-title">2016D</p>
                                    <p class="table-tracks"><sup>ALCA tracks</sup> 12K <span>@ 3.8T</span></p>
                                </div>

                                <div class="table-details">
                                    <ul>
                                        <li>run range: 276333 - 276804</li>
                                        <li>5 hours in total</li>
                                    </ul>
                                </div>
                            </div>
                        </div>

                        <div class="col-md-4 col-sm-12 col-xs-12">
                            <div class="my-table">
                                <div class="table-header">
                                    <p class="table-title">2016C</p>
                                    <p class="table-tracks"><sup>ALCA tracks</sup> 299K <span>@ 3.8T</span></p>
                                </div>

                                <div class="table-details">
                                    <ul>
                                        <li>run range: 275419 - 276310</li>
                                        <li>45 hours in total</li>
                                    </ul>
                                </div>
                            </div>
                        </div>

                        <div class="col-md-4 col-sm-12 col-xs-12">
                            <div class="my-table">
                                <div class="table-header">
                                    <p class="table-title">2016B</p>
                                    <p class="table-tracks"><sup>ALCA tracks</sup> 2515K <span>@ 3.8T</span></p>
                                </div>

                                <div class="table-details">
                                    <ul>
                                        <li>run range: 272118 - 275418</li>
                                        <li>300 hours in total</li>
                                    </ul>
                                </div>
                            </div>
                        </div>

                        <div class="col-md-4 col-sm-12 col-xs-12">
                            <div class="my-table">
                                <div class="table-header">
                                    <p class="table-title">CRUZET</p>
                                    <p class="table-tracks"><sup>ALCA tracks</sup> 58K <span>@ 0T</span></p>
                                </div>

                                <div class="table-details">
                                    <ul>
                                        <li>run range: 268730 - 269989</li>
                                        <li>14 hours in total</li>
                                    </ul>
                                </div>
                            </div>
                        </div>

                        <div class="col-md-4 col-sm-12 col-xs-12">
                            <div class="my-table">
                                <div class="table-header">
                                    <p class="table-title">CRUZET GR4</p>
                                    <p class="table-tracks"><sup>ALCA tracks</sup> 82K <span>@ 0T</span></p>
                                </div>

                                <div class="table-details">
                                    <ul>
                                        <li>run range: 267362 - 267878</li>
                                        <li>23 hours in total</li>
                                    </ul>
                                </div>
                            </div>
                        </div>

                        <div class="col-md-4 col-sm-12 col-xs-12">
                            <div class="my-table">
                                <div class="table-header">
                                    <p class="table-title">CRUZET GR3</p>
                                    <p class="table-tracks"><sup>ALCA tracks</sup> 356K <span>@ 0T</span></p>
                                </div>

                                <div class="table-details">
                                    <ul>
                                        <li>run range: 266134 - 266684 </li>
                                        <li>98 hours in total</li>
                                    </ul>
                                </div>
                            </div>
                        </div>

                    </div>
                </div>

            </div>
        </section>

        </body>
        </html>

""" % (time.ctime() , allAlcaTracks_currentH / 1000. , maxcosmicrunforstat , abs(allLumi_currentH * 23.31 / 3600.) )
    outCOSMICTRACKS = open("Cosmics16.hours.html", "w")
    outCOSMICTRACKS.write(htmlCOSMICTRACKS)
    outCOSMICTRACKS.close()

print "B " , allAlcaTracksB / 1000. , abs(allLumiB * 23.31 / 3600.) 
print "C " , allAlcaTracksC / 1000. , abs(allLumiC * 23.31 / 3600.) 
print "D " , allAlcaTracksD / 1000. , abs(allLumiD * 23.31 / 3600.) 
print "E " , allAlcaTracksE / 1000. , abs(allLumiE * 23.31 / 3600.) 
print "F " , allAlcaTracksF / 1000. , abs(allLumiF * 23.31 / 3600.) 
print "G " , allAlcaTracksG / 1000. , abs(allLumiG * 23.31 / 3600.) 
