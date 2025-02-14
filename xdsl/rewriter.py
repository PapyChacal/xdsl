from collections.abc import Sequence

from xdsl.ir import Block, BlockArgument, Operation, Region, SSAValue


class Rewriter:
    @staticmethod
    def erase_op(op: Operation, safe_erase: bool = True):
        """
        Erase an operation.
        Check that the operation has no uses, and has a parent.
        If safe_erase is True, check that the operation has no uses.
        Otherwise, replace its uses with ErasedSSAValue.
        """
        assert op.parent is not None, "Cannot erase an operation that has no parents"

        block = op.parent
        block.erase_op(op, safe_erase=safe_erase)

    @staticmethod
    def replace_op(
        op: Operation,
        new_ops: Operation | Sequence[Operation],
        new_results: Sequence[SSAValue | None] | None = None,  # noqa
        safe_erase: bool = True,
    ):
        """
        Replace an operation with multiple new ones.
        If new_results is specified, map the results of the deleted operations with these
        SSA values.
        Otherwise, use the results of the last operation added.
        None elements in new_results are the SSA values to delete.
        If safe_erase is False, then operations can be deleted even if they are
        still used.
        """
        if op.parent is None:
            raise ValueError("Cannot replace an operation without a parent")
        block = op.parent

        if isinstance(new_ops, Operation):
            new_ops = [new_ops]
        if new_results is None:
            new_results = [] if len(new_ops) == 0 else new_ops[-1].results

        if len(op.results) != len(new_results):
            raise ValueError(
                f"Expected {len(op.results)} new results, but got {len(new_results)}"
            )

        for old_result, new_result in zip(op.results, new_results):
            if new_result is None:
                old_result.erase(safe_erase=safe_erase)
            else:
                old_result.replace_by(new_result)

        block.insert_ops_after(new_ops, op)

        if len(op.results):
            for new_op in new_ops:
                for res in new_op.results:
                    res.name_hint = op.results[0].name_hint

        block.erase_op(op, safe_erase=safe_erase)

    @staticmethod
    def inline_block_at_end(inlined_block: Block, extended_block: Block):
        """
        Move the block operations to the end of another block.
        This block should not be a parent of the block to move to.
        The block operations should not use the block arguments.
        """
        if inlined_block.is_ancestor(extended_block):
            raise Exception("Cannot inline a block in a child block.")
        for op in inlined_block.ops:
            for operand in op.operands:
                if (
                    isinstance(operand, BlockArgument)
                    and operand.block is extended_block
                ):
                    raise Exception(
                        "Cannot inline block which has operations using "
                        "the block arguments."
                    )

        ops = list(inlined_block.ops)
        for block_op in ops:
            block_op.detach()

        extended_block.add_ops(ops)

    @staticmethod
    def inline_block_at_start(inlined_block: Block, extended_block: Block):
        """
        Move the block operations to the start of another block.
        This block should not be a parent of the block to move to.
        The block operations should not use the block arguments.
        """
        first_op_of_extended_block = extended_block.first_op
        if first_op_of_extended_block is None:
            Rewriter.inline_block_at_end(inlined_block, extended_block)
        else:
            Rewriter.inline_block_before(inlined_block, first_op_of_extended_block)

    @staticmethod
    def inline_block_before(
        source: Block, op: Operation, arg_values: Sequence[SSAValue] = ()
    ):
        """
        Move the block operations before another operation.
        The block should not be a parent of the operation.
        """
        # MLIR equivalent:
        # https://github.com/llvm/llvm-project/blob/96a3d05ed923d2abd51acb52984b83b9e8044924/mlir/lib/IR/PatternMatch.cpp#L290
        assert len(arg_values) == len(source.args), (
            f"Expected {len(source.args)} replacement argument values, got "
            f"{len(arg_values)}"
        )

        # The source block will be deleted, so it should not have any users (i.e.,
        # there should be no predecessors).
        # TODO: check that the block has no predecessors

        #  assert not block.predecessors, "expected 'source' to have no predecessors"

        if (dest := op.parent) is None:
            raise Exception("Cannot inline a block before a toplevel operation")

        # TODO: verify that the successors will make sense after inlining
        # We currently cannot perform this check, just like the TODO above, due to lack
        # of infrastructure in xDSL
        # https://github.com/xdslproject/xdsl/issues/2066

        # if dest.last_op != op:
        #       The source block will be inserted in the middle of the dest block, so the
        #       source block should have no successors. Otherwise, the remainder of the dest
        #       block would be unreachable.
        #       assert not source.successors, "expected 'source' to have no successors");
        # else:
        #       The source block will be inserted at the end of the dest block, so the dest
        #       block should have no successors. Otherwise, the inserted operations will be
        #       unreachable.
        #       assert not dest.successors,  "expected 'dest' to have no successors");

        # Replace all of the successor arguments with the provided values.
        for arg, val in zip(source.args, arg_values, strict=True):
            arg.replace_by(val)

        # Move operations from the source block to the dest block and erase the
        # source block.
        ops = list(source.ops)
        for block_op in ops:
            block_op.detach()

        dest.insert_ops_before(ops, op)
        parent_region = source.parent
        assert parent_region is not None
        parent_region.detach_block(source)
        source.erase()

    @staticmethod
    def inline_block_after(block: Block, op: Operation):
        """
        Move the block operations after another operation.
        The block should not be a parent of the operation.
        The block operations should not use the block arguments.
        """
        if op.parent is None:
            raise Exception("Cannot inline a block before a toplevel operation")

        ops = list(block.ops)
        for block_op in ops:
            block_op.detach()

        op.parent.insert_ops_after(ops, op)

    @staticmethod
    def insert_block_after(block: Block | list[Block], target: Block):
        """
        Insert one or multiple blocks after another block.
        The blocks to insert should be detached from any region.
        The target block should not be contained in the block to insert.
        """
        if target.parent is None:
            raise Exception("Cannot move a block after a toplevel op")
        region = target.parent
        block_list = block if isinstance(block, list) else [block]
        if len(block_list) == 0:
            return
        pos = region.get_block_index(target)
        region.insert_block(block_list, pos + 1)

    @staticmethod
    def insert_block_before(block: Block | list[Block], target: Block):
        """
        Insert one or multiple block before another block.
        The blocks to insert should be detached from any region.
        The target block should not be contained in the block to insert.
        """
        if target.parent is None:
            raise Exception("Cannot move a block after a toplevel op")
        region = target.parent
        block_list = block if isinstance(block, list) else [block]
        pos = region.get_block_index(target)
        region.insert_block(block_list, pos)

    @staticmethod
    def insert_op_after(op: Operation, new_op: Operation):
        """Inserts a new operation after another operation."""
        if op.parent is None:
            raise Exception("Cannot insert an operation after a toplevel operation")
        op.parent.insert_ops_after((new_op,), op)

    @staticmethod
    def insert_op_before(op: Operation, new_op: Operation):
        """Inserts a new operation before another operation."""
        if op.parent is None:
            raise Exception("Cannot insert an operation before a toplevel operation")
        op.parent.insert_ops_before((new_op,), op)

    @staticmethod
    def move_region_contents_to_new_regions(region: Region) -> Region:
        """Move the region blocks to a new region."""
        new_region = Region()
        for block in region.blocks:
            block.parent = None
            new_region.add_block(block)
        region.blocks = []
        return new_region

    @staticmethod
    def _inline_region_at_pos(region: Region, target: Region, pos: int) -> None:
        """Move the region blocks to an existing region, at position `pos`."""
        if region is target:
            raise ValueError("Cannot move region into itself.")
        for block in region.blocks:
            block.parent = None
        target.insert_block(region.blocks, pos)
        region.blocks = []

    @staticmethod
    def inline_region_before(region: Region, target: Block) -> None:
        """Move the region blocks to an existing region, before `target`."""
        parent_region = target.parent
        if parent_region is None:
            raise ValueError("Cannot inline region before a block with no parent")
        pos = parent_region.get_block_index(target)
        Rewriter._inline_region_at_pos(region, parent_region, pos)

    @staticmethod
    def inline_region_after(region: Region, target: Block) -> None:
        """Move the region blocks to an existing region, after `target`."""
        parent_region = target.parent
        if parent_region is None:
            raise ValueError("Cannot inline region before a block with no parent")
        pos = parent_region.get_block_index(target) + 1
        Rewriter._inline_region_at_pos(region, parent_region, pos)

    @staticmethod
    def inline_region_at_start(region: Region, target: Region) -> None:
        """Move the region blocks to the start of an existing region."""
        Rewriter._inline_region_at_pos(region, target, 0)

    @staticmethod
    def inline_region_at_end(region: Region, target: Region) -> None:
        """Move the region blocks to the end of an existing region."""
        Rewriter._inline_region_at_pos(region, target, len(target.blocks))
