from dataclasses import dataclass
from models import FileRecord

@dataclass
class SearchFilters:
    query: str = ''
    extension: str = ''
    category: str = 'All'
    min_size: int = 0
    max_size: int | None = None
    modified_after: float | None = None
    modified_before: float | None = None

    def matches(self, record: FileRecord) -> bool:
        extension = self.extension.strip().lower().lstrip('.')
        return (self.query.casefold() in record.path.name.casefold()
                and (not extension or record.extension.lstrip('.') == extension)
                and (self.category == 'All' or record.category == self.category)
                and record.size >= self.min_size
                and (self.max_size is None or record.size <= self.max_size)
                and (self.modified_after is None or record.modified >= self.modified_after)
                and (self.modified_before is None or record.modified < self.modified_before))

def search_files(records: list[FileRecord], filters: SearchFilters) -> list[FileRecord]:
    return [record for record in records if filters.matches(record)]
