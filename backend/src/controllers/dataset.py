from src.services import dataset as service
def list_items(): return service.list_datasets()
def detail(id): return service.detail(id)
def create(data): return service.create(data)
def rename(id,data): return service.rename(id,data)
def versions(id): return service.list_versions(id)
def batches(id): return service.list_batches(id)
def append_specimens(id,data,user): return service.apply_specimens(id,data,user["email"])
def withdraw_specimens(id,data,user): return service.apply_withdrawal(id,data,user["email"])
