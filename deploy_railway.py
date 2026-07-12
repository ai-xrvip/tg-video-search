import requests, json

token = "147a31d0-9ad5-4c87-b12b-5d23f9292bfd"
headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# Check current deployments
q1 = {"query": '{ deployments(input: {serviceId: "5b9429bc-f6f9-4e83-9e70-caf6e97b59f2"}) { edges { node { id status createdAt } } } }'}
r1 = requests.post("https://backboard.railway.app/graphql/v2", json=q1, headers=headers)
print("Current deployments:")
print(json.dumps(r1.json(), indent=2))

# Trigger deploy
q2 = {"query": 'mutation { serviceInstanceDeployV2(serviceId: "5b9429bc-f6f9-4e83-9e70-caf6e97b59f2", environmentId: "31cb2006-9a27-4a8e-8c19-a5eea8e8b960") }'}
r2 = requests.post("https://backboard.railway.app/graphql/v2", json=q2, headers=headers)
print("\nDeploy result:")
print(json.dumps(r2.json(), indent=2))
