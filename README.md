# pstats_viewer

An interactive browser for Python's cProfile output in pstats format.

Profile's cProfile module is great for collecting profiling data on Python
programs, but interpreting the output isn't easy. This tool allows for browsing
this data in a simple web-based tool.

Based on the
[original](https://chadaustin.me/2008/05/open-sourced-our-pstats-viewer/)
released by IMVU with some enhancements and fixes.

![screenshot of index](/docs/pstats_index.png?raw=true)
![screenshot of detail](/docs/pstats_detail.png?raw=true)

## Getting Started

### Prerequisities

There are no prerequisites for running pstats_viewer other than Python itself.

### Running the viewer

Running pstats_viewer on the cProfile output file of your choice will start a
local web server on port 4040:

```
pstats_viewer.py <stats.dat>
```

An alternate port number may also be provided:

```
pstats_viewer.py <stats.dat> <port_number>
```
