## Purpose

Use the hostkey API to find common and odd hardware combinations for testing programs and miners,
for example different CPUs:

32/64/128/256GB RAM options

i3/i5/i7 generations 11-15
Xeon E5/Dual Xeons
AMD Zen, Ryzen, Threadripper

NVIDIA GPU rtx3080,3090,4080,4090,5090,a-series, h-series, b-series
Radeon GPU XT7xxx/XTX7xxx/XT48xx Series

The basic idea is to rent the machine on per-hour basis with a standard linux installation:

run compilation and benchmark the application for 1 hours, output benchmarks and save to external source
rince and repeat for each interesting/useful hardware combination

It should be done automatically, and if possible in parallell, using the hostkey API: /stocks/#api-methods


For example, getting all the available stock: https://hostkey.com/documentation/apidocs/stocks/list

curl
 -s "https://api.hostkey.com/stocks.php" -X POST \

Results:
{
"result": "OK",
"action": "list",
"servers": [
  {
    "id": 1024,
    "name": "srv-nl-01",
    "location": "NL",
    "group": "dedicated",
    "status": "available",
    "price": {
      "EUR": 49.99,
      "USD": 54.99,
      "USD": 4500.0
    },
    "specs": {
      "cpu": "Intel Xeon",
      "ram": 32,
      "disk": 1000
    },
    "created_at": "2024-01-15T10:30:00Z",
    "server_group": "Dedicated Server",
    "tags": [],
    "deployment_eta": null,
    "billing_plan": {}
  }
]
}

curl -s "https://api.hostkey.com/stocks.php" -X POST \
--data "action=show" \
--data "id=VALUE"

{
"result": "OK",
"action": "show",
"server_data": {
  "id": 1024,
  "name": "srv-nl-01",
  "location": "NL",
  "group": "dedicated",
  "status": "available",
  "price": {
    "EUR": 49.99,
    "USD": 54.99,
    "USD": 4500.0
  },
  "specs": {
    "cpu": "Intel Xeon",
    "ram": 32,
    "disk": 1000
  },
  "created_at": "2024-01-15T10:30:00Z"
}
}
----------------------------------------

curl -s "https://api.hostkey.com/os.php" -X POST \
--data "action=list"
{
"result": "OK",
"action": "list",
"os_list": [
  {
    "id": 101,
    "name": "Ubuntu 22.04 LTS",
    "active": 1,
    "tags": [
      {
        "tag": "uefi",
        "value": "true"
      },
      {
        "tag": "vm",
        "value": "true"
      }
    ],
    "price_EUR": 0.0,
    "location": "NL",
    "billing_plan": "monthly",
    "cores": 2,
    "ram": 4096,
    "hdd": 80,
    "cpu_sockets": 1,
    "bm": 0,
    "gpu": 0,
    "vds": 0,
    "vgpu": 0
  },
  {
    "id": 102,
    "name": "Windows Server 2022",
    "active": 1,
    "tags": [
      {
        "tag": "uefi",
        "value": "true"
      },
      {
        "tag": "license",
        "value": "required"
      }
    ],
    "price_EUR": 15.0,
    "location": "DE",
    "billing_plan": "monthly",
    "cores": 4,
    "ram": 8192,
    "hdd": 160,
    "cpu_sockets": 1,
    "bm": 1,
    "gpu": 0,
    "vds": 0,
    "vgpu": 0
  }
]
}---------------------


Use the API to order https://hostkey.com/documentation/server_order/server_preorder/

Order via the API with discount: https://hostkey.com/documentation/server_order/stock_server_ordering/

## Implemented scripts

### 1) Discover and rank server configurations

`scripts/hostkey_matrix.py` queries:
- `stocks.php` with `action=list`
- `os.php` with `action=list`, `id=<server_id>`, `bill_period=hourly`

It keeps servers that are:
- `status=available`
- by default, currently listed stock entries (availability snapshot)

Optional strict mode:
- compatible with at least one Linux OS image on `hourly` billing (`REQUIRE_LINUX_HOURLY=1`)

Then it classifies by the variance goals:
- RAM focus: `32/64/128/256 GB`
- CPU families: Intel i3/i5/i7, Xeon, AMD Zen/Ryzen/Threadripper/EPYC
- GPU families: RTX 3080/3090/4080/4090/5090, NVIDIA A/H/B series, Radeon 7xxx/48xx patterns

Output is sorted by EUR price and saved to:
- `outputs/hostkey_candidates.json`
- `outputs/hostkey_candidates.csv`

Run:

```bash
chmod +x scripts/run_discovery.sh scripts/hostkey_matrix.py
./scripts/run_discovery.sh
```

Optional:

```bash
export HOSTKEY_TOKEN="your_token_if_needed"
export HOSTKEY_API_BASE="https://invapi.hostkey.com"
export WORKERS=20
./scripts/run_discovery.sh
```

Strict Linux hourly compatibility mode:

```bash
export REQUIRE_LINUX_HOURLY=1
./scripts/run_discovery.sh
```

Filter probing and debug logs:

```bash
# enabled by default in run_discovery.sh
export PROBE_FILTERS=1
export LOCATIONS="NL,DE,FI,US,SG"
export GROUPS="dedicated,gpu,Intel,AMD Ryzen"
./scripts/run_discovery.sh
```

Raw API responses and probe log are saved under:
- `outputs/debug/stocks_probe_log.json`
- `outputs/debug/stocks_<api>_<location>_<group>.json`

If no stock entries are returned, the script now writes empty outputs and exits successfully.

### 2) Benchmark automation with Ansible

Files:
- `ansible/playbook.yml`
- `ansible/inventory.ini`
- `ansible/benchmark_and_submit.sh.j2`

Flow on each target host:
1. install build dependencies
2. clone a benchmark repository from GitHub
3. compile it
4. run benchmark for one hour (configurable command)
5. annotate results with host metadata
6. submit JSON results to a third-party HTTP endpoint

Before running, edit these variables in `ansible/playbook.yml`:
- `benchmark_repo_url`
- `benchmark_build_cmd`
- `benchmark_run_cmd`
- `benchmark_submit_url`
- `benchmark_submit_token`

And set target hosts in `ansible/inventory.ini`.

Run:

```bash
ansible-playbook -i ansible/inventory.ini ansible/playbook.yml
```
