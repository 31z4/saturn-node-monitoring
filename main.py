import os
import signal
from collections import defaultdict
from datetime import datetime

import requests
from prometheus_client import (
    GC_COLLECTOR,
    PLATFORM_COLLECTOR,
    PROCESS_COLLECTOR,
    REGISTRY,
    start_http_server,
)
from prometheus_client.core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    InfoMetricFamily,
)


def _bool_to_str(v):
    return str(v).lower()


def _str_to_timestamp(v):
    ts = datetime.strptime(v, "%Y-%m-%dT%H:%M:%S.%fZ").timestamp()
    # Grafana expects Unix timestamps in milliseconds, not seconds.
    return ts * 1000


class NodeInfoMetric(InfoMetricFamily):
    def __init__(self):
        super().__init__("saturn_node", "Information about the node.")

    @staticmethod
    def _id_short(v):
        return v[:8]

    def add(self, node):
        values = {
            "id": node["id"],
            "id_short": self._id_short(node["id"]),
            "state": node["state"],
            "core": _bool_to_str(node["core"]),
            "ip_address": node["ipAddress"],
            "sunrise": _bool_to_str(node["sunrise"]),
            "cassini": _bool_to_str(node["cassini"]),
            "geoloc_region": node["geoloc"]["region"],
            "geoloc_city": node["geoloc"]["city"],
            "geoloc_country": node["geoloc"]["country"],
            "geoloc_country_code": node["geoloc"]["countryCode"],
        }

        speedtest = node.get("speedtest")
        if speedtest:
            values.update(
                {
                    "sppedtest_isp": node["speedtest"]["isp"],
                    "sppedtest_server_location": node["speedtest"]["server"][
                        "location"
                    ],
                    "sppedtest_server_country": node["speedtest"]["server"]["country"],
                }
            )

        self.add_metric([], values)

    def add_inactive(self, node_id):
        self.add_metric(
            [],
            {
                "id": node_id,
                "id_short": self._id_short(node_id),
                "state": "inactive",
            },
        )


class NodePayoutInfoMetric(InfoMetricFamily):
    def __init__(self):
        super().__init__("saturn_node_payout", "Payout status of the node.")

    def add(self, node):
        self.add_metric([], {"id": node["nodeId"], "status": node["payoutStatus"]})


class NodeVersionMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_version",
            "The version of the software the node is running.",
            labels=["id"],
        )

    def add(self, node):
        version = node["version"].split("_")[0]
        self.add_metric([node["id"]], version)


class NodeWeightMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_weight", "Weight of the node in the network.", labels=["id"]
        )

    def add(self, node):
        self.add_metric([node["id"]], node["bias"])


class NodeBiasMetric(GaugeMetricFamily):
    _BIASES = {
        "ageBias": "age",
        "ttfbBias": "ttfb",
        "randomBias": "random",
        "uptimeBias": "uptime",
        "speedtestBias": "speedtest",
    }

    def __init__(self):
        super().__init__(
            "saturn_node_bias",
            "Various bias values that affect the weight of the node in the network.",
            labels=["id", "kind"],
        )

    def add(self, node):
        for k, v in self._BIASES.items():
            bias = node["biases"].get(k)
            if bias is not None:
                self.add_metric([node["id"], v], bias)


class NodePenaltyMetric(GaugeMetricFamily):
    _PENALTIES = {
        "speedPenalty": "speed",
        "cpuLoadPenalty": "cpu_load",
        "errorRatioPenalty": "error_ratio",
        "oldVersionPenalty": "old_version",
        "cacheHitRatioPenalty": "cache_hit_ratio",
        "dupCacheMissRatioPenalty": "dup_cache_miss_ratio",
        "healthCheckFailuresPenalty": "health_check_failures",
    }

    def __init__(self):
        super().__init__(
            "saturn_node_penalty",
            "Various penalty values that affect the weight of the node in the network.",
            labels=["id", "kind"],
        )

    def add(self, node):
        for k, v in self._PENALTIES.items():
            penalty = node["biases"].get(k)
            if penalty is not None:
                self.add_metric([node["id"], v], penalty)


class NodeWeightedTTFBMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_weighted_ttfb_milliseconds",
            "Weighted time to first byte.",
            labels=["id"],
        )

    def add(self, node):
        v = node["biases"].get("weightedTtfb")
        if v is not None:
            self.add_metric([node["id"]], v)


class NodeWeightedHitsRatioMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_weighted_hits_ratio",
            "Weighted cache hits ratio of the node.",
            labels=["id"],
        )

    def add(self, node):
        v = node["biases"].get("weightedHitsRatio")
        if v is not None:
            self.add_metric([node["id"]], v)


class NodeWeightedErrorsRatioMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_weighted_errors_ratio",
            "Weighted error ratio of the node.",
            labels=["id"],
        )

    def add(self, node):
        v = node["biases"].get("weightedErrorsRatio")
        if v is not None:
            self.add_metric([node["id"]], v)


class NodeWeightedDupCacheMissRatioMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_weighted_dup_cache_miss_ratio",
            "Weighted duplicate cache miss ratio of the node.",
            labels=["id"],
        )

    def add(self, node):
        v = node["biases"].get("weightedDupCacheMissRatio")
        if v is not None:
            self.add_metric([node["id"]], v)


class NodeLastRegistrationMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_last_registration_timestamp",
            "When the node was last registered.",
            labels=["id"],
        )

    def add(self, node):
        last_registration_ts = _str_to_timestamp(node["lastRegistration"])
        self.add_metric([node["id"]], last_registration_ts)


class NodeCreationMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_creation_timestamp",
            "When the node was created.",
            labels=["id"],
        )

    def add(self, node):
        creation_ts = _str_to_timestamp(node["createdAt"])
        self.add_metric([node["id"]], creation_ts)


class NodeDiskTotalMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_disk_total_megabytes",
            "Total amount of storage on the node.",
            labels=["id"],
        )

    def add(self, node):
        self.add_metric([node["id"]], node["diskStats"]["totalDiskMB"])


class NodeDiskUsedMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_disk_used_megabytes",
            "The amount of storage used on the node.",
            labels=["id"],
        )

    def add(self, node):
        self.add_metric([node["id"]], node["diskStats"]["usedDiskMB"])


class NodeDiskAvailableMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_disk_available_megabytes",
            "The amount of storage available on the node.",
            labels=["id"],
        )

    def add(self, node):
        self.add_metric([node["id"]], node["diskStats"]["availableDiskMB"])


class NodeMemoryTotalMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_memory_total_kilobytes",
            "Total amount of RAM on the node.",
            labels=["id"],
        )

    def add(self, node):
        self.add_metric([node["id"]], node["memoryStats"]["totalMemoryKB"])


class NodeMemoryFreeMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_memory_free_kilobytes",
            "Free amount of RAM on the node.",
            labels=["id"],
        )

    def add(self, node):
        self.add_metric([node["id"]], node["memoryStats"]["freeMemoryKB"])


class NodeMemoryAvailableMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_memory_available_kilobytes",
            "The amount of RAM available on the node.",
            labels=["id"],
        )

    def add(self, node):
        self.add_metric([node["id"]], node["memoryStats"]["availableMemoryKB"])


class NodeCPUNumberMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_cpu_number",
            "The number of CPU cores on the node.",
            labels=["id"],
        )

    def add(self, node):
        self.add_metric([node["id"]], node["cpuStats"]["numCPUs"])


class NodeCPULoadAvgMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_cpu_load_avg", "CPU load average of the node.", labels=["id"]
        )

    def add(self, node):
        self.add_metric([node["id"]], node["cpuStats"]["loadAvgs"][0])


class NodeSentBytesTotalMetric(CounterMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_sent_bytes",
            "Total amount of traffic sent by the node.",
            labels=["id"],
        )

    def add(self, node):
        self.add_metric([node["id"]], node["nicStats"]["bytesSent"])


class NodeSpeedtestUploadBandwidthMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_speedtest_upload_bandwidth",
            "Node upload bandwidth as measured by Speedtest.",
            labels=["id"],
        )

    def add(self, node):
        speedtest = node.get("speedtest")
        if speedtest:
            self.add_metric([node["id"]], speedtest["upload"]["bandwidth"])


class NodeSpeedtestDownloadBandwidthMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_speedtest_download_bandwidth",
            "Node download bandwidth as measured by Speedtest.",
            labels=["id"],
        )

    def add(self, node):
        speedtest = node.get("speedtest")
        if speedtest:
            self.add_metric([node["id"]], speedtest["download"]["bandwidth"])


class NodeSpeedtestPingLatencyMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_speedtest_ping_latency_milliseconds",
            "Node ping latency as measured by Speedtest.",
            labels=["id"],
        )

    def add(self, node):
        speedtest = node.get("speedtest")
        if speedtest:
            self.add_metric([node["id"]], speedtest["ping"]["latency"])


class NodeReceivedBytesTotalMetric(CounterMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_received_bytes",
            "Total amount of traffic received by the node.",
            labels=["id"],
        )

    def add(self, node):
        self.add_metric([node["id"]], node["nicStats"]["bytesReceived"])


class NodeEstimatedEarningsMetric(CounterMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_estimated_earnings_fil",
            "Estimated earnings of the node.",
            labels=["id"],
        )

    def add(self, node):
        fil_amount = node.get("filAmount")
        if fil_amount is not None:
            self.add_metric([node["nodeId"]], fil_amount)


class NodeUptimeCompletionMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_uptime_completion_ratio",
            "Node uptime requirement completion ratio.",
            labels=["id"],
        )

    def add(self, node):
        uptime_completion = node.get("uptimeCompletion")
        if uptime_completion is not None:
            self.add_metric([node["nodeId"]], uptime_completion)


class NodeRetrievalsMetric(CounterMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_retrievals",
            "The number of retrievals served by the node.",
            labels=["id"],
        )

    def add(self, node):
        num_requests = node.get("numRequests")
        if num_requests is not None:
            self.add_metric([node["nodeId"]], num_requests)


class NodeBandwidthServedMetric(CounterMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_bandwidth_served_bytes",
            "The amount of traffic served by the node.",
            labels=["id"],
        )

    def add(self, node):
        num_bytes = node.get("numBytes")
        if num_bytes is not None:
            self.add_metric([node["nodeId"]], num_bytes)


class NodeResponseDurationMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_response_duration_milliseconds",
            "The time it takes by average for the node to respond to a request.",
            labels=["id", "quantile"],
        )

    def add(self, node):
        ttfb = node.get("ttfbStats")
        if not ttfb:
            return

        for q in (0.01, 0.05, 0.5, 0.95, 0.99):
            p = int(q * 100)
            try:
                self.add_metric([node["id"], str(q)], ttfb[f"p{p}_1h"])
            except KeyError:
                return


class NodeRequestsMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_requests",
            "The number of requests served by the node.",
            labels=["id", "result"],
        )

    def add(self, node):
        ttfb = node.get("ttfbStats")
        if not ttfb:
            return

        try:
            ok = ttfb["reqs_served_1h"]
            hits = ttfb["hits_1h"]
            errors = ttfb["errors_1h"]
            slow_hits = ttfb["slow_hits_1h"]
        except KeyError:
            return

        self.add_metric([node["id"], "ok"], ok)
        self.add_metric([node["id"], "ok_hit"], hits)
        self.add_metric([node["id"], "error"], errors)
        self.add_metric([node["id"], "ok_slow_hit"], slow_hits)


class NodeHealthCheckFailuresMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_health_check_failures",
            "The number of node health check failures.",
            labels=["id", "error"],
        )

    def add(self, node):
        failures = node.get("HealthCheckFailures")
        if not failures:
            self.add_metric([node["id"]], 0)
            return

        errors = defaultdict(int)
        for f in failures:
            errors[f["error"]] += 1

        for k, v in errors.items():
            self.add_metric([node["id"], k], v)


class NodeRequirementsMinCPUCoresMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_requirements_min_cpu_cores",
            "The minimum number of CPU cores required for a node.",
        )

    def add(self, requirements):
        self.add_metric([], requirements["minCPUCores"])


class NodeRequirementsMinMemoryMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_requirements_min_memory_gigabytes",
            "The minimum amount of RAM required for a node.",
        )

    def add(self, requirements):
        self.add_metric([], requirements["minMemoryGB"])


class NodeRequirementsMinUploadSpeedMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_requirements_min_upload_speed_mbps",
            "The minimum upload speed required for a node.",
        )

    def add(self, requirements):
        self.add_metric([], requirements["minUploadSpeedMbps"])


class NodeRequirementsMinDownloadSpeedMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_requirements_min_download_speed_mbps",
            "The minimum download speed required for a node.",
        )

    def add(self, requirements):
        self.add_metric([], requirements["minDownloadSpeedMbps"])


class NodeRequirementsMinDiskMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_requirements_min_disk_gigabytes",
            "The minimum amount of storage required for a node.",
        )

    def add(self, requirements):
        self.add_metric([], requirements["minDiskGB"])


class NodeRequirementsLastVersionMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_requirements_last_version",
            "The latest version of the node's software.",
        )

    def add(self, requirements):
        self.add_metric([], requirements["lastVersion"])


class NodeRequirementsMinVersionMetric(GaugeMetricFamily):
    def __init__(self):
        super().__init__(
            "saturn_node_requirements_min_version",
            "The minimum required version of the node's software.",
        )

    def add(self, requirements):
        self.add_metric([], requirements["minVersion"])


class StatsCollector:
    def __init__(self, node_ids):
        """Collects stats for the specified node IDs.

        If node_ids is empty then collets stats for all nodes.
        """
        self._node_ids = frozenset(node_ids)

    def _node_metrics_from_stats(self, stats):
        info = NodeInfoMetric()
        metrics = (
            info,
            NodeVersionMetric(),
            NodeWeightMetric(),
            NodeBiasMetric(),
            NodePenaltyMetric(),
            NodeWeightedTTFBMetric(),
            NodeWeightedHitsRatioMetric(),
            NodeWeightedErrorsRatioMetric(),
            NodeWeightedDupCacheMissRatioMetric(),
            NodeLastRegistrationMetric(),
            NodeCreationMetric(),
            NodeDiskTotalMetric(),
            NodeDiskUsedMetric(),
            NodeDiskAvailableMetric(),
            NodeMemoryTotalMetric(),
            NodeMemoryFreeMetric(),
            NodeMemoryAvailableMetric(),
            NodeCPUNumberMetric(),
            NodeCPULoadAvgMetric(),
            NodeResponseDurationMetric(),
            NodeRequestsMetric(),
            NodeHealthCheckFailuresMetric(),
            NodeSentBytesTotalMetric(),
            NodeReceivedBytesTotalMetric(),
            NodeSpeedtestUploadBandwidthMetric(),
            NodeSpeedtestDownloadBandwidthMetric(),
            NodeSpeedtestPingLatencyMetric(),
        )

        found = set()
        for node in stats:
            if self._node_ids and node["id"] not in self._node_ids:
                continue
            found.add(node["id"])

            for m in metrics:
                m.add(node)

        # Every not found node considered inactive.
        for i in self._node_ids - found:
            info.add_inactive(i)

        return metrics

    def collect(self):
        r = requests.get(
            "https://orchestrator.strn.pl/stats",
            headers={"Accept": "application/json", "Accept-Encoding": "gzip, deflate"},
        )
        stats = r.json()

        for m in self._node_metrics_from_stats(stats["nodes"]):
            yield m


class EarningsAndRetrievalsCollector:
    def __init__(self, node_ids):
        """Collects earnings and retrievals for the specified node IDs.

        If node_ids is empty then collets stats for all nodes.
        """
        self._node_ids = frozenset(node_ids)
        self._start_ts = self._utcnow_timestamp()

    @staticmethod
    def _utcnow_timestamp():
        return datetime.utcnow().timestamp() * 1000

    def _node_earnings_and_retrievals_metrics(self, earnings):
        metrics = (
            NodePayoutInfoMetric(),
            NodeEstimatedEarningsMetric(),
            NodeUptimeCompletionMetric(),
            NodeRetrievalsMetric(),
            NodeBandwidthServedMetric(),
        )

        for node in earnings:
            if self._node_ids and node["nodeId"] not in self._node_ids:
                continue

            for m in metrics:
                m.add(node)

        return metrics

    def collect(self):
        r = requests.get(
            "https://uc2x7t32m6qmbscsljxoauwoae0yeipw.lambda-url.us-west-2.on.aws",
            params={
                "filAddress": "all",
                "startDate": self._start_ts,
                "endDate": self._utcnow_timestamp(),
                "step": "day",
                "perNode": "true",
            },
        )
        earnings = r.json()

        for m in self._node_earnings_and_retrievals_metrics(earnings["perNodeMetrics"]):
            yield m


class RequirementsCollector:
    @staticmethod
    def _node_requirements_metrics(requirements):
        metrics = (
            NodeRequirementsMinCPUCoresMetric(),
            NodeRequirementsMinMemoryMetric(),
            NodeRequirementsMinUploadSpeedMetric(),
            NodeRequirementsMinDownloadSpeedMetric(),
            NodeRequirementsMinDiskMetric(),
            NodeRequirementsLastVersionMetric(),
            NodeRequirementsMinVersionMetric(),
        )

        for m in metrics:
            m.add(requirements)

        return metrics

    def collect(self):
        r = requests.get("https://orchestrator.strn.pl/requirements")
        requirements = r.json()

        for m in self._node_requirements_metrics(requirements):
            yield m


if __name__ == "__main__":
    # Disable default collector metrics.
    REGISTRY.unregister(GC_COLLECTOR)
    REGISTRY.unregister(PLATFORM_COLLECTOR)
    REGISTRY.unregister(PROCESS_COLLECTOR)

    node_ids = []
    # Try reading node IDs from file set in SATURN_PROMETHEUS_EXPORTER_NODES.
    nodes_file = os.environ.get("SATURN_PROMETHEUS_EXPORTER_NODES")
    if nodes_file:
        with open(nodes_file) as f:
            node_ids = [line.strip() for line in f]

    REGISTRY.register(StatsCollector(node_ids))
    REGISTRY.register(EarningsAndRetrievalsCollector(node_ids))
    REGISTRY.register(RequirementsCollector())

    start_http_server(9000)

    signal.pause()
