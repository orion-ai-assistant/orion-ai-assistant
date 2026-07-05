param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("up", "up-lb", "up-ai", "build", "down", "logs", "ps", "restart", "test")]
    [string]$Command,
    [string]$BaseUrl = "http://localhost:8909"
)

# Kullanım:
# .\scripts\orion.cmd build
# .\scripts\orion.cmd up

$dev = "docker-compose.dev.yml"
$lb = "docker-compose.lb.yml"
$llm = "docker-compose.llm.yml"

switch ($Command) {
    "build" {
        docker compose -f $dev build
    }
    "up" {
        docker compose -f $dev up --build -d
    }
    "up-lb" {
        docker compose -f $dev -f $lb up --build -d
    }
    "up-ai" {
        docker compose -f $dev -f $llm up --build -d
    }
    "down" {
        docker compose -f $dev -f $lb -f $llm down --remove-orphans
    }
    "logs" {
        docker compose -f $dev -f $lb -f $llm logs -f --tail=200
    }
    "ps" {
        docker compose -f $dev -f $lb -f $llm ps
    }
    "restart" {
        docker compose -f $dev -f $lb -f $llm restart
    }
    "test" {
        $env:ORION_BASE_URL = $BaseUrl
        python .\scripts\load_test.py
    }
}
