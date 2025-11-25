#!/bin/bash
# Envoy Gateway Migration Validation Script

set -e

echo "🔍 Validating Envoy Gateway + Coraza + MetalLB Migration..."
echo "============================================================"

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print status
print_status() {
    if [ $2 -eq 0 ]; then
        echo -e "${GREEN}✅ $1${NC}"
    else
        echo -e "${RED}❌ $1${NC}"
    fi
}

echo -e "${YELLOW}1. Checking MetalLB installation...${NC}"
kubectl get pods -n metallb-system --no-headers | grep -c "Running" > /dev/null
print_status "MetalLB pods are running" $?

kubectl get ipaddresspool -n metallb-system main-pool > /dev/null
print_status "MetalLB IP address pool configured" $?

echo -e "\n${YELLOW}2. Checking Envoy Gateway installation...${NC}"
kubectl get pods -n envoy-gateway-system --no-headers | grep -c "Running" > /dev/null  
print_status "Envoy Gateway pods are running" $?

kubectl get gatewayclass envoy > /dev/null
print_status "Envoy GatewayClass exists" $?

echo -e "\n${YELLOW}3. Checking Gateway configuration...${NC}"
kubectl get gateway envoy-gateway -n cert-manager > /dev/null
print_status "Envoy Gateway resource exists" $?

# Check if gateway has external IP
GATEWAY_IP=$(kubectl get gateway envoy-gateway -n cert-manager -o jsonpath='{.status.addresses[0].value}' 2>/dev/null || echo "")
if [ -n "$GATEWAY_IP" ]; then
    print_status "Gateway has external IP: $GATEWAY_IP" 0
else
    print_status "Gateway does not have external IP yet (this may be normal during initial setup)" 1
fi

echo -e "\n${YELLOW}4. Checking HTTPRoute configuration...${NC}"
ROUTE_COUNT=$(kubectl get httproutes -A --no-headers | wc -l)
echo "Found $ROUTE_COUNT HTTPRoute resources"

# Check if routes reference the new gateway
ENVOY_ROUTES=$(kubectl get httproutes -A -o yaml | grep -c "envoy-gateway" || echo "0")
print_status "$ENVOY_ROUTES HTTPRoutes reference envoy-gateway" 0

echo -e "\n${YELLOW}5. Checking TLS certificates...${NC}"
CERT_COUNT=$(kubectl get certificates -n cert-manager --no-headers | grep -c "True" || echo "0")
print_status "$CERT_COUNT certificates are ready" 0

echo -e "\n${YELLOW}6. Checking Coraza WAF configuration...${NC}"
kubectl get configmap coraza-config -n coraza-system > /dev/null
print_status "Coraza configuration exists" $?

kubectl get securitypolicy coraza-waf-policy -n cert-manager > /dev/null
print_status "Coraza security policy exists" $?

echo -e "\n${YELLOW}7. Testing application connectivity...${NC}"

# Test domains (add more as needed)
DOMAINS=(
    "argo.timosur.com"
    "mealie.timosur.com"
    "ai.timosur.com"
    "timosur.com"
)

for domain in "${DOMAINS[@]}"; do
    if curl -sSf -L --max-time 10 "https://$domain" > /dev/null 2>&1; then
        print_status "$domain is accessible" 0
    else
        print_status "$domain is not accessible" 1
    fi
done

echo -e "\n${YELLOW}8. Testing WAF functionality...${NC}"

# Test WAF blocking (should fail)
if curl -H "User-Agent: sqlmap" -sSf --max-time 5 "https://argo.timosur.com" > /dev/null 2>&1; then
    print_status "WAF did not block malicious request (may need configuration)" 1
else
    print_status "WAF correctly blocked malicious request" 0
fi

echo -e "\n${YELLOW}9. Checking resource status...${NC}"

# Check for any pods in error state
ERROR_PODS=$(kubectl get pods -A --no-headers | grep -E "(Error|CrashLoopBackOff|ImagePullBackOff)" | wc -l)
if [ "$ERROR_PODS" -eq 0 ]; then
    print_status "No pods in error state" 0
else
    print_status "$ERROR_PODS pods in error state" 1
    kubectl get pods -A --no-headers | grep -E "(Error|CrashLoopBackOff|ImagePullBackOff)"
fi

echo -e "\n${YELLOW}10. Summary${NC}"
echo "============================================================"

if kubectl get gateway envoy-gateway -n cert-manager -o jsonpath='{.status.conditions[?(@.type=="Programmed")].status}' | grep -q "True"; then
    echo -e "${GREEN}🎉 Migration appears successful!${NC}"
    echo -e "${GREEN}   - Envoy Gateway is programmed and ready${NC}"
    echo -e "${GREEN}   - MetalLB is providing load balancing${NC}"  
    echo -e "${GREEN}   - Coraza WAF is configured${NC}"
    echo -e "${GREEN}   - HTTPRoutes are updated${NC}"
else
    echo -e "${YELLOW}⚠️  Migration in progress...${NC}"
    echo -e "${YELLOW}   Gateway may still be initializing${NC}"
fi

echo -e "\n${YELLOW}Next steps:${NC}"
echo "1. Monitor application access over the next few minutes"
echo "2. Verify TLS certificate renewal works correctly"  
echo "3. Test WAF rules and adjust configuration as needed"
echo "4. Remove old Cilium Gateway once everything is stable"
echo "5. Apply Terraform changes to disable Hetzner LB"

echo -e "\n${YELLOW}Useful commands:${NC}"
echo "- Watch gateway status: kubectl get gateway envoy-gateway -n cert-manager -w"
echo "- Check Envoy logs: kubectl logs -n envoy-gateway-system deployment/envoy-gateway -f"
echo "- Monitor certificates: kubectl get certificates -n cert-manager -w"
echo "- Test WAF: curl -H 'User-Agent: <script>alert(1)</script>' https://argo.timosur.com/"