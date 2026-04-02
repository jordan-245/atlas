"""Atlas AI Overlay module.

Three-component AI tightening layer that sits on top of the quantitative
regime model.  Can only reduce exposure — never increase beyond the regime
default.

Components
----------
overlay.engine     — Claude-powered decision maker (Builder 1)
overlay.sources    — Data aggregators: charts, alt-data, news (Builder 2)
overlay.evaluator  — Weekly self-evaluation of past decisions (Builder 3)
overlay.cron       — Daily cron entry point (Builder 3)
"""
