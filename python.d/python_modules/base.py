# -*- coding: utf-8 -*-
# Description: prototypes for netdata python.d modules
# Author: Pawel Krupa (paulfantom)

import time
import sys
try:
    from urllib.request import urlopen
except ImportError:
    from urllib2 import urlopen

import threading
import msg


class BaseService(threading.Thread):
    """
    Prototype of Service class.
    Implemented basic functionality to run jobs by `python.d.plugin`
    """
    def __init__(self, configuration=None, name=None):
        """
        This needs to be initialized in child classes
        :param configuration: dict
        :param name: str
        """
        threading.Thread.__init__(self)
        self.data_stream = ""
        self.daemon = True
        self.retries = 0
        self.retries_left = 0
        self.priority = 140000
        self.update_every = 1
        self.name = name
        self.override_name = None
        self.chart_name = ""
        self.dimensions = []
        self.charts = []
        if configuration is None:
            self.error("BaseService: no configuration parameters supplied. Cannot create Service.")
            raise RuntimeError
        else:
            self._extract_base_config(configuration)
            self.timetable = {}
            self.create_timetable()

    def _extract_base_config(self, config):
        """
        Get basic parameters to run service
        Minimum config:
            config = {'update_every':1,
                      'priority':100000,
                      'retries':0}
        :param config: dict
        """
        try:
            self.override_name = config.pop('override_name')
        except KeyError:
            pass
        self.update_every = int(config.pop('update_every'))
        self.priority = int(config.pop('priority'))
        self.retries = int(config.pop('retries'))
        self.retries_left = self.retries
        self.configuration = config

    def create_timetable(self, freq=None):
        """
        Create service timetable.
        `freq` is optional
        Example:
            timetable = {'last': 1466370091.3767564,
                         'next': 1466370092,
                         'freq': 1}
        :param freq: int
        """
        if freq is None:
            freq = self.update_every
        now = time.time()
        self.timetable = {'last': now,
                          'next': now - (now % freq) + freq,
                          'freq': freq}

    def _run_once(self):
        """
        Executes self.update(interval) and draws run time chart.
        Return value presents exit status of update()
        :return: boolean
        """
        t_start = time.time()
        # check if it is time to execute job update() function
        if self.timetable['next'] > t_start:
            msg.debug(self.chart_name + " will be run in " +
                      str(int((self.timetable['next'] - t_start) * 1000)) + " ms")
            return True

        since_last = int((t_start - self.timetable['last']) * 1000000)
        msg.debug(self.chart_name +
                  " ready to run, after " + str(int((t_start - self.timetable['last']) * 1000)) +
                  " ms (update_every: " + str(self.timetable['freq'] * 1000) +
                  " ms, latency: " + str(int((t_start - self.timetable['next']) * 1000)) + " ms)")
        if not self.update(since_last):
            return False
        t_end = time.time()
        self.timetable['next'] = t_end - (t_end % self.timetable['freq']) + self.timetable['freq']

        # draw performance graph
        run_time = str(int((t_end - t_start) * 1000))
        run_time_chart = "BEGIN netdata.plugin_pythond_" + self.chart_name + " " + str(since_last) + '\n'
        run_time_chart += "SET run_time = " + run_time + '\n'
        run_time_chart += "END\n"
        sys.stdout.write(run_time_chart)
        msg.debug(self.chart_name + " updated in " + str(run_time) + " ms")
        self.timetable['last'] = t_start
        return True

    def run(self):
        """
        Runs job in thread. Handles retries.
        Exits when job failed or timed out.
        :return: None
        """
        self.timetable['last'] = time.time()
        while True:
            try:
                status = self._run_once()
            except Exception as e:
                msg.error("Something wrong: " + str(e))
                return
            if status:
                time.sleep(self.timetable['next'] - time.time())
                self.retries_left = self.retries
            else:
                self.retries -= 1
                if self.retries_left <= 0:
                    msg.error("no more retries. Exiting")
                    return
                else:
                    time.sleep(self.timetable['freq'])

    def _line(self, instruction, *params):
        """
        Converts *params to string and joins them with one space between every one.
        :param params: str/int/float
        """
        self.data_stream += instruction
        for p in params:
            p = str(p)
            if len(p) == 0:
                p = "''"
            if ' ' in p:
                p = "'" + p + "'"
            self.data_stream += " " + p
        self.data_stream += "\n"

    def chart(self, type_id, name="", title="", units="", family="",
              category="", charttype="line", priority="", update_every=""):
        """
        Defines a new chart.
        :param type_id: str
        :param name: str
        :param title: str
        :param units: str
        :param family: str
        :param category: str
        :param charttype: str
        :param priority: int/str
        :param update_every: int/str
        """
        self.charts.append(type_id)

        self._line("CHART", type_id, name, title, units, family, category, charttype, priority, update_every)

    def dimension(self, id, name=None, algorithm="absolute", multiplier=1, divisor=1, hidden=False):
        """
        Defines a new dimension for the chart
        :param id: str
        :param name: str
        :param algorithm: str
        :param multiplier: int/str
        :param divisor: int/str
        :param hidden: boolean
        :return:
        """
        try:
            int(multiplier)
        except TypeError:
            self.error("malformed dimension: multiplier is not a number:", multiplier)
            multiplier = 1
        try:
            int(divisor)
        except TypeError:
            self.error("malformed dimension: divisor is not a number:", divisor)
            divisor = 1
        if name is None:
            name = id
        if algorithm not in ("absolute", "incremental", "percentage-of-absolute-row", "percentage-of-incremental-row"):
            algorithm = "absolute"

        self.dimensions.append(id)
        if hidden:
            self._line("DIMENSION", id, name, algorithm, multiplier, divisor, "hidden")
        else:
            self._line("DIMENSION", id, name, algorithm, multiplier, divisor)

    def begin(self, type_id, microseconds=0):
        """
        Begin data set
        :param type_id: str
        :param microseconds: int
        :return: boolean
        """
        if type_id not in self.charts:
            self.error("wrong chart type_id:", type_id)
            return False
        try:
            int(microseconds)
        except TypeError:
            self.error("malformed begin statement: microseconds are not a number:", microseconds)
            microseconds = ""

        self._line("BEGIN", type_id, microseconds)
        return True

    def set(self, id, value):
        """
        Set value to dimension
        :param id: str
        :param value: int/float
        :return: boolean
        """
        if id not in self.dimensions:
            self.error("wrong dimension id:", id)
            return False
        try:
            value = str(int(value))
        except TypeError:
            self.error("cannot set non-numeric value:", value)
            return False
        self._line("SET", id, "=", value)
        return True

    def end(self):
        self._line("END")

    def commit(self):
        """
        Upload new data to netdata
        """
        print(self.data_stream)
        self.data_stream = ""

    def error(self, *params):
        """
        Show error message on stderr
        """
        msg.error(self.chart_name, *params)

    def debug(self, *params):
        """
        Show debug message on stderr
        """
        msg.debug(self.chart_name, *params)

    def info(self, *params):
        """
        Show information message on stderr
        """
        msg.info(self.chart_name, *params)

    def check(self):
        """
        check() prototype
        :return: boolean
        """
        msg.error("Service " + str(self.__module__) + "doesn't implement check() function")
        return False

    def create(self):
        """
        create() prototype
        :return: boolean
        """
        msg.error("Service " + str(self.__module__) + "doesn't implement create() function?")
        return False

    def update(self, interval):
        """
        update() prototype
        :param interval: int
        :return: boolean
        """
        msg.error("Service " + str(self.__module__) + "doesn't implement update() function")
        return False


class UrlService(BaseService):
    def __init__(self, configuration=None, name=None):
        self.charts = {}
        # charts definitions in format:
        # charts = {
        #    'chart_name_in_netdata': {
        #        'options': "parameters defining chart (passed to CHART statement)",
        #        'lines': [
        #           { 'name': 'dimension_name',
        #             'options': 'dimension parameters (passed to DIMENSION statement)"
        #           }
        #        ]}
        #    }
        self.order = []
        self.definitions = {}
        # definitions are created dynamically in create() method based on 'charts' dictionary. format:
        # definitions = {
        #     'chart_name_in_netdata' : [ charts['chart_name_in_netdata']['lines']['name'] ]
        # }
        self.url = ""
        BaseService.__init__(self, configuration=configuration, name=name)

    def _get_data(self):
        """
        Get raw data from http request
        :return: str
        """
        raw = None
        try:
            f = urlopen(self.url, timeout=self.update_every)
            raw = f.read().decode('utf-8')
        except Exception as e:
            msg.error(self.__module__, str(e))
        finally:
            try:
                f.close()
            except:
                pass
        return raw

    def _formatted_data(self):
        """
        Format data received from http request
        :return: dict
        """
        return {}

    def check(self):
        """
        Format configuration data and try to connect to server
        :return: boolean
        """
        if self.name is None or self.name == str(None):
            self.name = 'local'
        else:
            self.name = str(self.name)
        try:
            self.url = str(self.configuration['url'])
        except (KeyError, TypeError):
            pass

        if self._formatted_data() is not None:
            return True
        else:
            return False

    def create(self):
        """
        Create charts
        :return: boolean
        """
        for name in self.order:
            if name not in self.charts:
                continue
            self.definitions[name] = []
            for line in self.charts[name]['lines']:
                self.definitions[name].append(line['name'])

        idx = 0
        data = self._formatted_data()
        if data is None:
            return False
        data_stream = ""
        for name in self.order:
            header = "CHART " + \
                     self.__module__ + "_" + \
                     self.name + "." + \
                     name + " " + \
                     self.charts[name]['options'] + " " + \
                     str(self.priority + idx) + " " + \
                     str(self.update_every)
            content = ""
            # check if server has this datapoint
            for line in self.charts[name]['lines']:
                if line['name'] in data:
                    content += "\nDIMENSION " + line['name'] + " " + line['options']

            if len(content) > 0:
                data_stream += header + content + "\n"
                idx += 1

        print(data_stream)

        if idx == 0:
            return False
        return True

    def update(self, interval):
        """
        Update charts
        :param interval: int
        :return: boolean
        """
        data = self._formatted_data()
        if data is None:
            return False

        data_stream = ""
        for chart, dimensions in self.definitions.items():
            header = "BEGIN " + self.__module__ + "_" + str(self.name) + "." + chart + " " + str(interval)
            c = ""
            for dim in dimensions:
                try:
                    c += "\nSET " + dim + " = " + str(data[dim])
                except KeyError:
                    pass
            if len(c) != 0:
                data_stream += header + c + "\nEND\n"
        print(data_stream)

        return True
