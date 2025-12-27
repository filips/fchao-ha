# FCHAO Off-grid inverter HA MQTT Bridge

This repository contains code to communicate with an FCHAO Off-grid inverter through the built-in RS485 port. I have personally used a 1500W model, but it should work with all of the 1200-3500W units in the same series.

Thanks to https://github.com/taste66/FCHAO-Inverter for reverse engineering the RS485 commands for data readout. I did some further experimentation to find two commands that can disable/enable the AC output.

It will expose the sensor readings to MQTT, along with the proper metadata required to get Home Assistant to display the entities.