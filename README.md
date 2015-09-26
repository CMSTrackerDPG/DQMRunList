# DQMRunList
script for DQM shift crew that produces tables with run certification overview, separately for collision and cosmic runs.

Usage:
`python listRuns.py --min XXX [--max YYY] [--cosmic]`

The script is currently running on `vocms061` each hour by a cron. Tables are published here:
`http://vocms061.cern.ch/event_display/RunList/`
