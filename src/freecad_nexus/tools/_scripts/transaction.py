"""Document transactions shared by specialized FreeCAD commands."""

from contextlib import contextmanager

import FreeCAD as App

_mcp_transaction_label = None


def bom_cell_snapshots(document):
    """Native BOM clearAll() does not reliably restore custom cells on abort."""
    return {
        obj.Name: {
            address: obj.getContents(address) for address in obj.getNonEmptyCells()
        }
        for obj in document.Objects
        if obj.isDerivedFrom("Assembly::BomObject")
    }


def restore_bom_cells(document, snapshots):
    for name, cells in snapshots.items():
        bom = document.getObject(name)
        if bom is None:
            raise RuntimeError("Native rollback did not restore BOM: " + name)
        current = {
            address: bom.getContents(address) for address in bom.getNonEmptyCells()
        }
        for address in current.keys() - cells.keys():
            bom.clear(address)
        for address, contents in cells.items():
            if current.get(address) != contents:
                bom.set(address, contents)
        # Evaluate cells without regenerating native rows or custom data.
        used = bom.getUsedRange()
        if used:
            bom.recomputeCells(*used)


def mcp_resume_transaction():
    """Resume the current MCP transaction after native GUI document switches."""
    document = App.ActiveDocument
    if (
        _mcp_transaction_label
        and document
        and not document.HasPendingTransaction
        and not App.getActiveTransaction()
    ):
        document.openTransaction(_mcp_transaction_label)


@contextmanager
def mcp_transaction(label):
    """Restore Undo mode and roll back failures without taking over user edits."""
    global _mcp_transaction_label
    document = App.ActiveDocument
    undo_mode = document.UndoMode if document else None
    active_transaction = App.getActiveTransaction()
    if active_transaction:
        raise RuntimeError(
            "Finish the pending FreeCAD transaction before this operation"
        )
    if document:
        if document.HasPendingTransaction:
            raise RuntimeError(
                "Finish the pending FreeCAD transaction before this operation"
            )
        # Console documents default to UndoMode=0, where abortTransaction
        # does not roll back changes. Enable it for the duration of this call.
        if not undo_mode:
            document.UndoMode = 1
        undo_count = document.UndoCount
        original_names = {obj.Name for obj in document.Objects}
        bom_cells = bom_cell_snapshots(document)
        document.openTransaction(label)
    _mcp_transaction_label = label
    try:
        yield
    except Exception:
        if document:
            document.abortTransaction()
            App.closeActiveTransaction(True)
            # GUI document switches during ToolBit construction can commit
            # the application-wide transaction. Undo only entries made since
            # this synchronous command started, retaining earlier user edits.
            for _ in range(max(0, document.UndoCount - undo_count)):
                document.undo()
            App.closeActiveTransaction(True)
            # Native Job callbacks can create a fresh default Stock during
            # undo, outside the reverted transaction. Remove only additions
            # absent at entry, preserving restored original object names.
            for _ in range(3):
                additions = [
                    obj.Name
                    for obj in document.Objects
                    if obj.Name not in original_names
                ]
                if not additions:
                    break
                for name in additions:
                    document.removeObject(name)
            if any(obj.Name not in original_names for obj in document.Objects):
                raise RuntimeError(
                    "Native rollback callbacks kept creating new objects"
                )
            restore_bom_cells(document, bom_cells)
        raise
    else:
        if document:
            document.commitTransaction()
            App.closeActiveTransaction()
    finally:
        _mcp_transaction_label = None
        if document and not undo_mode:
            document.UndoMode = undo_mode
