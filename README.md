# Raspberry Pi Water Management System

Course: Fundamentals of Software Development for Electronics

An automated irrigation/water-tank controller for Raspberry Pi, with a full
hardware simulation mode for development on a laptop.

## Quick start
    python -m venv .venv
    pip install -r requirements.txt
    python src/water_system/config.py

## Run modes
- `WATER_MOCK_MODE=auto` (default): mock on laptops, real on a Raspberry Pi
- `WATER_MOCK_MODE=1`: force simulation
- `WATER_MOCK_MODE=0`: force real hardware