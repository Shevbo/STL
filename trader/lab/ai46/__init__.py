"""MOEX AI Trading Bot — team-46.

Faithful Python re-creation of SkrimerForever/moex-trading-bot (go-bot) for the
Shectory platform. Runs as a privileged backend strategy (NOT a sandboxed on_bar
robot): it needs live order flow, statistical models and an LLM gate, all of which
the robot sandbox (trader/lab/script_guard.py) forbids.

Module map (Go -> Python):
  features.py  <- internal/features/{indicators,stochastic,volume_profile,
                  support_resistance,ofi,ict_structure,pivot_points}.go
"""
