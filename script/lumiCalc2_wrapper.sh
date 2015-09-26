#!/bin/bash
export SCRAM_ARCH=slc6_amd64_gcc491
eval $(scramv1 runtime -sh)
lumiCalc2.py -r $1 -b stable overview -o lumi.tmp
