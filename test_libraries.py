print("Starting test...")

import pandas as pd
print("pandas OK", pd.__version__)

import numpy as np
print("numpy OK", np.__version__)

import schedule
print("schedule OK")

import requests
print("requests OK", requests.__version__)

from kiteconnect import KiteConnect
print("kiteconnect OK")

import sqlite3
print("sqlite3 OK")

print("\nAll libraries working!")