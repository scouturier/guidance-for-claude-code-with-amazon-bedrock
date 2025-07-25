# ABOUTME: Status command to show deployment status and usage
# ABOUTME: Displays current state, usage metrics, and health checks

"""Status command - Show deployment status."""

import subprocess
import json
from pathlib import Path
from typing import Dict, Any, Optional
from cleo.commands.command import Command
from cleo.helpers import option, argument
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from claude_code_with_bedrock.config import Config
from claude_code_with_bedrock.cli.utils.aws import get_stack_outputs, check_stack_exists


class StatusCommand(Command):
    name = "status"
    description = "Show current deployment status and usage metrics"
    
    options = [
        option(
            "profile",
            description="Configuration profile to check",
            flag=False,
            default="default"
        ),
        option(
            "json",
            description="Output in JSON format",
            flag=True
        ),
        option(
            "detailed",
            description="Show detailed information",
            flag=True
        )
    ]
    
    def handle(self) -> int:
        """Execute the status command."""
        console = Console()
        
        # Load configuration
        config = Config.load()
        profile_name = self.option("profile")
        profile = config.get_profile(profile_name)
        
        if not profile:
            console.print(f"[red]Profile '{profile_name}' not found. Run 'poetry run ccwb init' first.[/red]")
            return 1
        
        # Get options
        json_output = self.option("json")
        detailed = self.option("detailed")
        
        if json_output:
            return self._show_json_status(profile, console)
        else:
            return self._show_rich_status(profile, console, detailed)
    
    def _show_rich_status(self, profile, console: Console, detailed: bool) -> int:
        """Show status in rich formatted output."""
        # Header
        console.print(Panel.fit(
            "[bold cyan]Claude Code with Bedrock - Deployment Status[/bold cyan]",
            border_style="cyan",
            padding=(1, 2)
        ))
        
        # Configuration section
        console.print("\n[bold]Configuration[/bold]")
        config_table = Table(box=box.SIMPLE)
        config_table.add_column("Setting", style="dim")
        config_table.add_column("Value")
        
        config_table.add_row("Profile", profile.name)
        config_table.add_row("OIDC Provider", profile.provider_domain)
        config_table.add_row("AWS Region", profile.aws_region)
        config_table.add_row("Identity Pool", profile.identity_pool_name)
        config_table.add_row("Monitoring", "✓ Enabled" if profile.monitoring_enabled else "✗ Disabled")
        config_table.add_row("Bedrock Regions", ", ".join(profile.allowed_bedrock_regions))
        
        console.print(config_table)
        
        # Stack status section
        console.print("\n[bold]Stack Status[/bold]")
        stacks = self._get_stack_status(profile)
        
        stack_table = Table(box=box.SIMPLE)
        stack_table.add_column("Stack", style="cyan")
        stack_table.add_column("Status")
        stack_table.add_column("Last Updated")
        
        for stack_type, info in stacks.items():
            status_color = "green" if info["status"] == "CREATE_COMPLETE" else "yellow"
            stack_table.add_row(
                stack_type.title(),
                f"[{status_color}]{info['status']}[/{status_color}]",
                info.get("last_updated", "N/A")
            )
        
        console.print(stack_table)
        
        # Endpoints section
        console.print("\n[bold]Endpoints[/bold]")
        endpoints = self._get_endpoints(profile)
        
        if endpoints.get("identity_pool_id"):
            console.print(f"• Identity Pool: [cyan]{endpoints['identity_pool_id']}[/cyan]")
        
        if endpoints.get("role_arn"):
            console.print(f"• Bedrock Role: [cyan]{endpoints['role_arn']}[/cyan]")
        
        if endpoints.get("oidc_provider"):
            console.print(f"• OIDC Provider: [cyan]{endpoints['oidc_provider']}[/cyan]")
        
        if profile.monitoring_enabled and endpoints.get("monitoring_endpoint"):
            console.print(f"\n• Monitoring Endpoint: [cyan]{endpoints['monitoring_endpoint']}[/cyan]")
            console.print(f"  Authentication: [dim]Bearer token (Cognito ID token)[/dim]")
            console.print(f"  Protocol: [dim]OTLP HTTP/Protobuf[/dim]")
        
        if endpoints.get("dashboard_url"):
            console.print(f"\n• CloudWatch Dashboard: [cyan]{endpoints['dashboard_url']}[/cyan]")
        
        # Package info
        dist_dir = Path.home() / "claude-code-with-bedrock" / "dist"
        if dist_dir.exists():
            console.print(f"\n[bold]Distribution Package[/bold]")
            console.print(f"• Location: [cyan]{dist_dir}[/cyan]")
            
            # Check if settings.json exists
            settings_file = dist_dir / ".claude" / "settings.json"
            if settings_file.exists():
                console.print(f"• Claude Settings: [green]✓ Configured[/green]")
            else:
                console.print(f"• Claude Settings: [yellow]⚠ Not found[/yellow]")
        
        # Next steps
        if detailed:
            console.print("\n[bold]Next Steps[/bold]")
            if not dist_dir.exists():
                console.print("1. Run [cyan]poetry run ccwb package[/cyan] to create distribution")
            console.print("2. Distribute package to users")
            console.print("3. Users run ./install.sh")
            
            # Show test commands
            console.print("\n[bold]Test Commands[/bold]")
            console.print("• Test authentication: [dim]export AWS_PROFILE=ClaudeCode && aws sts get-caller-identity[/dim]")
            console.print("• Get monitoring token: [dim]poetry run ccwb get-monitoring-token[/dim]")
        
        return 0
    
    def _show_json_status(self, profile, console: Console) -> int:
        """Show status in JSON format."""
        status = {
            "profile": profile.name,
            "configuration": {
                "oidc_provider": profile.provider_domain,
                "aws_region": profile.aws_region,
                "identity_pool": profile.identity_pool_name,
                "monitoring_enabled": profile.monitoring_enabled,
                "bedrock_regions": profile.allowed_bedrock_regions
            },
            "stacks": self._get_stack_status(profile),
            "endpoints": self._get_endpoints(profile)
        }
        
        console.print(json.dumps(status, indent=2))
        return 0
    
    def _get_stack_status(self, profile) -> Dict[str, Any]:
        """Get status of all stacks."""
        stacks = {}
        
        # Check auth stack
        auth_stack = profile.stack_names.get("auth", f"{profile.identity_pool_name}-stack")
        auth_status = self._check_stack(auth_stack, profile.aws_region)
        stacks["auth"] = auth_status
        
        if profile.monitoring_enabled:
            # Check monitoring stack
            monitoring_stack = profile.stack_names.get("monitoring", f"{profile.identity_pool_name}-monitoring")
            stacks["monitoring"] = self._check_stack(monitoring_stack, profile.aws_region)
            
            # Check dashboard stack
            dashboard_stack = profile.stack_names.get("dashboard", f"{profile.identity_pool_name}-dashboard")
            stacks["dashboard"] = self._check_stack(dashboard_stack, profile.aws_region)
        
        return stacks
    
    def _check_stack(self, stack_name: str, region: str) -> Dict[str, Any]:
        """Check individual stack status."""
        try:
            cmd = [
                "aws", "cloudformation", "describe-stacks",
                "--stack-name", stack_name,
                "--region", region,
                "--query", "Stacks[0].[StackStatus,LastUpdatedTime]",
                "--output", "json"
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode == 0:
                data = json.loads(result.stdout)
                return {
                    "status": data[0] if data else "NOT_FOUND",
                    "last_updated": data[1] if len(data) > 1 else None
                }
        except:
            pass
        
        return {"status": "NOT_FOUND", "last_updated": None}
    
    def _get_endpoints(self, profile) -> Dict[str, Any]:
        """Get all relevant endpoints."""
        endpoints = {}
        
        # Get auth stack outputs
        auth_stack = profile.stack_names.get("auth", f"{profile.identity_pool_name}-stack")
        auth_outputs = get_stack_outputs(auth_stack, profile.aws_region)
        
        if auth_outputs:
            endpoints["identity_pool_id"] = auth_outputs.get("IdentityPoolId")
            endpoints["role_arn"] = auth_outputs.get("BedrockRoleArn")
            endpoints["oidc_provider"] = auth_outputs.get("OIDCProviderArn")
        
        if profile.monitoring_enabled:
            # Get monitoring endpoint
            monitoring_stack = profile.stack_names.get("monitoring", f"{profile.identity_pool_name}-otel-collector")
            monitoring_outputs = get_stack_outputs(monitoring_stack, profile.aws_region)
            
            if monitoring_outputs:
                endpoints["monitoring_endpoint"] = monitoring_outputs.get("CollectorEndpoint")
            
            # Get dashboard URL
            dashboard_stack = profile.stack_names.get("dashboard", f"{profile.identity_pool_name}-dashboard")
            dashboard_outputs = get_stack_outputs(dashboard_stack, profile.aws_region)
            
            if dashboard_outputs:
                endpoints["dashboard_url"] = dashboard_outputs.get("DashboardURL")
        
        return endpoints